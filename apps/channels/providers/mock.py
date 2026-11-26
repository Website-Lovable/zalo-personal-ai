"""In-memory provider for tests and local development. Never talks to Zalo."""

from __future__ import annotations

import hmac
import uuid
from typing import Any

from .base import ChannelProvider, InboundMessage, SendResult


class MockChannelProvider(ChannelProvider):
    channel_type = "mock"
    #: Every send is recorded here: (channel_id, recipient_id, text).
    sent: list[tuple[int, str, str]] = []

    SECRET_HEADER = "X-Mock-Secret"  # noqa: S105 (header name, not a secret)

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        expected = str(self.channel.config.get("webhook_secret", ""))
        given = headers.get(self.SECRET_HEADER, "")
        return bool(expected) and hmac.compare_digest(expected, given)

    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        return [
            InboundMessage(
                sender_id=str(item["sender_id"]),
                text=item.get("text", ""),
                message_id=str(item.get("message_id") or uuid.uuid4()),
                sender_name=item.get("sender_name", ""),
                raw=item,
            )
            for item in payload.get("messages", [])
        ]

    def send_text(self, recipient_id: str, text: str) -> SendResult:
        MockChannelProvider.sent.append((self.channel.pk, recipient_id, text))
        return SendResult(ok=True, message_id=f"mock-{uuid.uuid4()}")

    @classmethod
    def reset(cls):
        cls.sent.clear()
