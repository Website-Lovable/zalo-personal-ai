"""Zalo Bot Platform provider (official Bot API).

Reference: https://bot.zaloplatforms.com/docs (checked 2026-11-26). Methods are called as
``POST {ZALO_BOT_API_URL}/bot<BOT_TOKEN>/<method>`` and answer ``{"ok": bool, "result": ...}``
(errors: ``{"ok": false, "error_code": int, "description": str}``).

- Webhook: ``setWebhook(url, secret_token)``; Zalo then sends the secret in the
  ``X-Bot-Api-Secret-Token`` header of every update (secret: 8-256 chars).
- Polling: ``getUpdates(timeout)``; does not work while a webhook is set, so the two
  delivery modes are mutually exclusive (``deleteWebhook`` switches to polling).
- ``sendMessage(chat_id, text)``, text 1-2000 characters.

The bot token is stored encrypted in ``Channel.access_token`` (it does not expire).
"""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

from .base import ChannelEvent, ChannelProvider, InboundMessage, SendResult
from .http import ZaloAPIError, ZaloAuthError, request_json

SECRET_HEADER = "X-Bot-Api-Secret-Token"  # noqa: S105 (header name, not a secret)
MAX_TEXT_LENGTH = 2000
AUTH_ERROR_CODES = {401, 403}

_MESSAGE_TYPES = {
    "message.text.received": "text",
    "message.image.received": "image",
    "message.sticker.received": "sticker",
    "message.voice.received": "audio",
}


def new_secret_token() -> str:
    return secrets.token_urlsafe(32)  # 43 URL-safe chars, within Zalo's 8-256 limit


class ZaloBotClient:
    def __init__(self, bot_token: str):
        if not bot_token:
            raise ZaloAuthError("No bot token stored for this channel")
        self._token = bot_token

    def call(self, method: str, payload: dict[str, Any] | None = None, **kwargs) -> Any:
        data = request_json(
            "POST",
            f"{settings.ZALO_BOT_API_URL}/bot{self._token}/{method}",
            json=payload or {},
            **kwargs,
        )
        if not isinstance(data, dict) or not data.get("ok"):
            code = data.get("error_code") if isinstance(data, dict) else None
            desc = data.get("description", "") if isinstance(data, dict) else ""
            error_class = ZaloAuthError if code in AUTH_ERROR_CODES else ZaloAPIError
            raise error_class(f"Zalo Bot API {method} failed: {desc}", code=code, payload=data)
        return data.get("result")

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe") or {}

    def set_webhook(self, url: str, secret_token: str) -> dict[str, Any]:
        return self.call("setWebhook", {"url": url, "secret_token": secret_token}) or {}

    def delete_webhook(self) -> Any:
        return self.call("deleteWebhook")

    def get_webhook_info(self) -> dict[str, Any]:
        return self.call("getWebhookInfo") or {}

    def get_updates(self, timeout: int) -> list[dict[str, Any]]:
        result = self.call(
            "getUpdates",
            {"timeout": str(timeout)},
            timeout=timeout + settings.ZALO_HTTP_TIMEOUT_SECONDS,
        )
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        return self.call("sendMessage", {"chat_id": chat_id, "text": text}) or {}


def _ms_to_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


class ZaloBotProvider(ChannelProvider):
    channel_type = "zalo_bot"

    @property
    def client(self) -> ZaloBotClient:
        return ZaloBotClient(self.channel.access_token)

    def verify_webhook(self, headers, body: bytes) -> bool:
        expected = self.channel.webhook_secret
        given = headers.get(SECRET_HEADER) or ""
        return bool(expected) and hmac.compare_digest(expected.encode(), given.encode())

    def parse_webhook(self, payload):
        return [e.message for e in self.parse_events(payload) if e.message]

    def parse_events(self, payload: dict[str, Any]) -> list[ChannelEvent]:
        # Webhook bodies are wrapped as {"ok": true, "result": {...}}; getUpdates items may
        # be the bare update. Accept both.
        update = payload.get("result", payload) if isinstance(payload, dict) else {}
        if not isinstance(update, dict):
            return []
        event_name = str(update.get("event_name", ""))
        message = update.get("message") or {}
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        msg_id = str(message.get("message_id") or "")
        dedupe_key = msg_id or f"{event_name}:{chat.get('id')}:{message.get('date')}"
        message_type = _MESSAGE_TYPES.get(event_name)
        # Phase 2 handles 1-1 chats only; group chats and bots are ignored on purpose.
        if (
            message_type is None
            or str(chat.get("chat_type", "PRIVATE")).upper() != "PRIVATE"
            or sender.get("is_bot")
        ):
            return [ChannelEvent(kind="ignore", event_name=event_name, dedupe_key=dedupe_key)]
        chat_id = str(chat.get("id") or sender.get("id") or "")
        text = str(message.get("text") or message.get("caption") or "")
        if not text:
            detail = message.get("photo") or message.get("sticker") or message.get("voice_url")
            label = {"image": "Ảnh", "sticker": "Sticker", "audio": "Âm thanh"}.get(message_type)
            text = f"[{label}] {detail or ''}".strip()
        inbound = InboundMessage(
            sender_id=chat_id,
            text=text,
            message_id=dedupe_key,
            sent_at=_ms_to_datetime(message.get("date")),
            sender_name=str(sender.get("display_name") or ""),
            message_type=message_type,
            raw=update,
        )
        return [
            ChannelEvent(
                kind="message",
                event_name=event_name,
                dedupe_key=dedupe_key,
                user_id=chat_id,
                message=inbound,
            )
        ]

    def send_text(self, recipient_id: str, text: str) -> SendResult:
        if not text or len(text) > MAX_TEXT_LENGTH:
            return SendResult(ok=False, error=f"Text must be 1-{MAX_TEXT_LENGTH} characters")
        try:
            result = self.client.send_message(recipient_id, text)
        except ZaloAPIError as exc:
            return SendResult(ok=False, error=str(exc), error_code=exc.code)
        return SendResult(ok=True, message_id=str(result.get("message_id", "")))
