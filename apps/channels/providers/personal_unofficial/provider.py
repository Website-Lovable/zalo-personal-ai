"""Provider for personal Zalo accounts. Every send goes through the guard in ``limits.py``.

Inbound events arrive from the sidecar (``webhook.py``) in the sidecar's own event format,
not in a Zalo format, so the core never depends on the login library.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..base import ChannelEvent, ChannelProvider, InboundMessage, SendResult
from .adapter import AdapterSendResult, RestrictionDetected, SidecarError, get_adapter
from .limits import check_send

SIGNATURE_HEADER = "X-ZP-Signature"
TIMESTAMP_HEADER = "X-ZP-Timestamp"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(headers, body: bytes, secret: str | None = None) -> bool:
    """HMAC-SHA256 over ``"<timestamp>.<body>"`` with the shared sidecar secret."""
    secret = secret if secret is not None else settings.ZALO_PERSONAL_SIDECAR_SECRET
    timestamp = headers.get(TIMESTAMP_HEADER, "")
    given = headers.get(SIGNATURE_HEADER, "")
    if not (secret and timestamp and given):
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > settings.ZALO_PERSONAL_SIGNATURE_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), given)


class PersonalUnofficialProvider(ChannelProvider):
    channel_type = "zalo_personal"

    def verify_webhook(self, headers, body: bytes) -> bool:
        return verify_signature(headers, body)

    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        return [e.message for e in self.parse_events(payload) if e.message is not None]

    def parse_events(self, payload: dict[str, Any]) -> list[ChannelEvent]:
        """Sidecar event ``{"type": "message", "event_id", "message": {...}}``.

        Group chats are ignored (1-1 only); ``is_self`` marks the owner's own messages typed
        on the phone, stored as outbound so the conversation copy stays complete.
        """
        if payload.get("type") != "message":
            return []
        msg = payload.get("message") or {}
        if msg.get("thread_type") != "user":
            return [
                ChannelEvent(
                    kind="ignore",
                    event_name="group_message",
                    dedupe_key=str(payload.get("event_id", "")),
                )
            ]
        msg_id = str(msg.get("msg_id") or payload.get("event_id") or "")
        thread_id = str(msg.get("thread_id") or "")
        text = msg.get("text")
        is_text = isinstance(text, str)
        sent_at = None
        if msg.get("ts"):
            try:
                sent_at = datetime.fromtimestamp(int(msg["ts"]) / 1000, tz=UTC)
            except (TypeError, ValueError):
                sent_at = None
        inbound = InboundMessage(
            sender_id=thread_id,
            text=text if is_text else "",
            message_id=msg_id,
            sent_at=sent_at,
            sender_name="" if msg.get("is_self") else str(msg.get("from_name") or "")[:255],
            message_type="text" if is_text else "other",
            raw={},
        )
        return [
            ChannelEvent(
                kind="message",
                event_name="message",
                dedupe_key=msg_id,
                user_id=thread_id,
                message=inbound,
                outbound=bool(msg.get("is_self")),
            )
        ]

    def send_text(self, recipient_id: str, text: str) -> SendResult:
        from apps.channels.models import Channel
        from apps.contacts.models import Contact

        from . import control

        with transaction.atomic():
            # Row lock: serializes sends of one channel so the caps cannot be overrun.
            channel = Channel.objects.select_for_update().get(pk=self.channel.pk)
            contact = Contact.objects.filter(
                tenant=channel.tenant, channel=channel, zalo_user_id=recipient_id
            ).first()
            decision = check_send(channel, contact)
            if not decision.allowed:
                return SendResult(
                    ok=False,
                    error=f"guard:{decision.reason}: {decision.message}",
                    retry_after_seconds=decision.retry_after_seconds,
                )
            try:
                result = get_adapter().send_text(channel, recipient_id, text)
            except RestrictionDetected as exc:
                restriction = exc
            except SidecarError as exc:
                restriction, result = None, AdapterSendResult(ok=False, error=str(exc))
            else:
                restriction = None
                if result.ok:
                    # Written under the lock: the next sender sees it before our Message row
                    # exists, so the minimum gap (and therefore the caps) hold across workers.
                    config = dict(channel.config or {})
                    config["last_sent_at"] = timezone.now().isoformat()
                    Channel.objects.filter(pk=channel.pk).update(config=config)
                    channel.config = self.channel.config = config
        if restriction is not None:
            control.handle_restriction(channel, restriction)
            return SendResult(ok=False, error=f"restricted:{restriction.kind}")
        if not result.ok:
            control.record_send_failure(channel, result.error)
            return SendResult(ok=False, error=result.error)
        control.record_send_success(channel)
        return SendResult(ok=True, message_id=result.message_id)
