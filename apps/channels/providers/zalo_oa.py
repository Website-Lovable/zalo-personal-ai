"""Zalo Official Account provider (official OA Open API v3 + OAuth v4).

Reference: https://developers.zalo.me/docs/official-account (checked 2026-11-26; the official
pages render client-side, so details were cross-checked with several public integrations).
Values that could change are settings, not constants: see ``ZALO_OA_*`` in settings.

Webhook signature (header ``X-ZEvent-Signature``)::

    mac = sha256(app_id + raw_body + timestamp + oa_secret_key)  (hex)

``app_id`` and ``timestamp`` are read from the JSON body, the body is hashed exactly as
received, and the secret is the "OA Secret Key" from the app's Webhook settings page.
The header value may or may not carry a ``mac=`` prefix; both are accepted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone

from .base import ChannelEvent, ChannelProvider, InboundMessage, SendResult
from .http import ZaloAPIError, ZaloAuthError, request_json

SIGNATURE_HEADER = "X-ZEvent-Signature"
#: OA API error codes meaning "access token invalid/expired": refresh once, then retry.
TOKEN_ERROR_CODES = {-216, -124}
MAX_TEXT_LENGTH = 2000

# event_name -> Message.MessageType for user_send_* / oa_send_* events
_MESSAGE_TYPES = {
    "text": "text",
    "image": "image",
    "gif": "image",
    "file": "file",
    "sticker": "sticker",
    "location": "location",
    "audio": "audio",
    "video": "video",
    "link": "other",
    "business_card": "other",
}


# --- OAuth v4 (PKCE) --------------------------------------------------------------------


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: datetime


def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) with challenge = base64url(sha256(verifier))."""
    verifier = secrets.token_urlsafe(32)  # 43 chars
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorize_url(*, state: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "app_id": settings.ZALO_OA_APP_ID,
            "redirect_uri": settings.ZALO_OA_OAUTH_REDIRECT_URI,
            "code_challenge": code_challenge,
            "state": state,
        }
    )
    return f"{settings.ZALO_OA_OAUTH_URL}/permission?{query}"


def _token_request(form: dict[str, str]) -> TokenSet:
    data = request_json(
        "POST",
        f"{settings.ZALO_OA_OAUTH_URL}/access_token",
        headers={"secret_key": settings.ZALO_OA_APP_SECRET},
        data={"app_id": settings.ZALO_OA_APP_ID, **form},
    )
    if not isinstance(data, dict) or not data.get("access_token"):
        code = data.get("error") if isinstance(data, dict) else None
        desc = ""
        if isinstance(data, dict):
            desc = data.get("error_description") or data.get("error_name") or ""
        raise ZaloAuthError(f"Zalo OAuth token request failed: {desc}", code=code, payload=data)
    expires_in = int(data.get("expires_in") or settings.ZALO_OA_DEFAULT_TOKEN_TTL_SECONDS)
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=timezone.now() + timedelta(seconds=expires_in),
    )


def exchange_code(code: str, code_verifier: str) -> TokenSet:
    return _token_request(
        {"code": code, "code_verifier": code_verifier, "grant_type": "authorization_code"}
    )


def refresh_tokens(refresh_token: str) -> TokenSet:
    """Refresh tokens are single use: always store the new refresh token returned."""
    if not refresh_token:
        raise ZaloAuthError("No refresh token stored for this channel")
    return _token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})


# --- Provider ---------------------------------------------------------------------------


def _ms_to_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def oa_id_from_payload(payload: dict[str, Any]) -> str:
    """The OA an event belongs to: ``oa_id``, else recipient (user_send_*) or sender (oa_*)."""
    if payload.get("oa_id"):
        return str(payload["oa_id"])
    event_name = str(payload.get("event_name", ""))
    side = "sender" if event_name.startswith("oa_") else "recipient"
    return str((payload.get(side) or {}).get("id", ""))


def compute_signature(app_id: str, body: bytes, timestamp: str, secret: str) -> str:
    raw = app_id.encode() + body + timestamp.encode() + secret.encode()
    return hashlib.sha256(raw).hexdigest()


class ZaloOAProvider(ChannelProvider):
    channel_type = "zalo_oa"

    # -- webhook --------------------------------------------------------------------------

    def webhook_secret(self) -> str:
        return self.channel.webhook_secret or settings.ZALO_OA_WEBHOOK_SECRET

    def verify_webhook(self, headers, body: bytes) -> bool:
        secret = self.webhook_secret()
        given = (headers.get(SIGNATURE_HEADER) or "").replace(" ", "").lower()
        if not secret or not given:
            return False
        given = given.removeprefix("mac=")
        try:
            payload = json.loads(body)
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        app_id = str(payload.get("app_id", ""))
        expected_app_id = self.channel.zalo_app_id or settings.ZALO_OA_APP_ID
        if expected_app_id and app_id != expected_app_id:
            return False
        expected = compute_signature(app_id, body, str(payload.get("timestamp", "")), secret)
        return hmac.compare_digest(expected, given)

    def parse_webhook(self, payload):
        return [e.message for e in self.parse_events(payload) if e.message and not e.outbound]

    def parse_events(self, payload: dict[str, Any]) -> list[ChannelEvent]:
        event_name = str(payload.get("event_name", ""))
        timestamp = str(payload.get("timestamp", ""))
        if event_name in ("follow", "unfollow"):
            user_id = str((payload.get("follower") or {}).get("id", ""))
            return [
                ChannelEvent(
                    kind=event_name,
                    event_name=event_name,
                    dedupe_key=f"{event_name}:{user_id}:{timestamp}",
                    user_id=user_id,
                )
            ]
        for prefix, outbound in (("user_send_", False), ("oa_send_", True)):
            if event_name.startswith(prefix):
                kind = event_name.removeprefix(prefix)
                return [self._message_event(payload, event_name, kind, outbound)]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return [
            ChannelEvent(
                kind="ignore",
                event_name=event_name,
                dedupe_key=f"{event_name}:{timestamp}:{fingerprint[:16]}",
            )
        ]

    def _message_event(self, payload, event_name, kind, outbound) -> ChannelEvent:
        message = payload.get("message") or {}
        user_side = "recipient" if outbound else "sender"
        user_id = str((payload.get(user_side) or {}).get("id", ""))
        msg_id = str(message.get("msg_id") or f"{event_name}:{user_id}:{payload.get('timestamp')}")
        message_type = _MESSAGE_TYPES.get(kind, "other")
        text = str(message.get("text") or "")
        if not text:
            text = _describe_attachments(message.get("attachments") or [], message_type)
        inbound = InboundMessage(
            sender_id=user_id,
            text=text,
            message_id=msg_id,
            sent_at=_ms_to_datetime(payload.get("timestamp")),
            message_type=message_type,
            raw=payload,
        )
        return ChannelEvent(
            kind="message",
            event_name=event_name,
            dedupe_key=msg_id,
            user_id=user_id,
            message=inbound,
            outbound=outbound,
        )

    # -- outbound -------------------------------------------------------------------------

    def _api(self, method: str, path: str, *, retry_on_token_error=True, **kwargs) -> dict:
        from apps.channels.services import ensure_fresh_oa_token, refresh_oa_token

        token = ensure_fresh_oa_token(self.channel)
        data = request_json(
            method,
            f"{settings.ZALO_OA_API_URL}{path}",
            headers={"access_token": token},
            **kwargs,
        )
        code = data.get("error", 0) if isinstance(data, dict) else None
        if code in TOKEN_ERROR_CODES and retry_on_token_error:
            refresh_oa_token(self.channel, force=True)
            return self._api(method, path, retry_on_token_error=False, **kwargs)
        if code in TOKEN_ERROR_CODES:
            raise ZaloAuthError(str(data.get("message", "invalid token")), code=code, payload=data)
        if not isinstance(data, dict) or code != 0:
            message = data.get("message", "") if isinstance(data, dict) else ""
            raise ZaloAPIError(f"Zalo OA API error: {message}", code=code, payload=data)
        return data

    def send_text(self, recipient_id: str, text: str) -> SendResult:
        if not text or len(text) > MAX_TEXT_LENGTH:
            return SendResult(ok=False, error=f"Text must be 1-{MAX_TEXT_LENGTH} characters")
        try:
            data = self._api(
                "POST",
                "/v3.0/oa/message/cs",
                json={"recipient": {"user_id": recipient_id}, "message": {"text": text}},
            )
        except ZaloAPIError as exc:
            return SendResult(ok=False, error=str(exc), error_code=exc.code)
        return SendResult(ok=True, message_id=str((data.get("data") or {}).get("message_id", "")))

    def get_user_detail(self, user_id: str) -> dict[str, Any]:
        data = self._api(
            "GET", "/v3.0/oa/user/detail", params={"data": json.dumps({"user_id": user_id})}
        )
        return data.get("data") or {}

    def list_user_ids(self, *, offset: int, count: int) -> tuple[list[str], int]:
        """One page of the OA's users. Returns (user_ids, total)."""
        query = {"offset": offset, "count": count, "is_follower": "true"}
        data = self._api("GET", "/v3.0/oa/user/getlist", params={"data": json.dumps(query)})
        body = data.get("data") or {}
        users = [str(u.get("user_id")) for u in body.get("users", []) if u.get("user_id")]
        return users, int(body.get("total") or 0)


def _describe_attachments(attachments: list[dict], message_type: str) -> str:
    labels = {
        "image": "Ảnh",
        "file": "Tệp",
        "sticker": "Sticker",
        "location": "Vị trí",
        "audio": "Âm thanh",
        "video": "Video",
    }
    label = labels.get(message_type, "Tin nhắn")
    if not attachments:
        return f"[{label}]"
    payload = attachments[0].get("payload") or {}
    if message_type == "location" and payload.get("coordinates"):
        coords = payload["coordinates"]
        return f"[{label}] {coords.get('latitude')},{coords.get('longitude')}"
    detail = payload.get("name") or payload.get("url") or payload.get("id") or ""
    return f"[{label}] {detail}".strip()
