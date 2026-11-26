"""Abstract channel provider. The core pipeline only talks to this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class InboundMessage:
    sender_id: str
    text: str
    message_id: str
    sent_at: datetime | None = None
    sender_name: str = ""
    message_type: str = "text"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelEvent:
    """A provider event normalized for the core pipeline.

    kind: ``message`` | ``follow`` | ``unfollow`` | ``ignore``.
    ``outbound`` marks an echo of a message the account itself sent (e.g. staff replying from
    the Zalo OA manager), so the conversation copy stays complete.
    """

    kind: str
    event_name: str
    dedupe_key: str
    user_id: str = ""
    message: InboundMessage | None = None
    outbound: bool = False


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str = ""
    error: str = ""
    error_code: int | None = None


class ChannelProvider(ABC):
    channel_type: str = ""
    #: False for providers that can only draft (the owner sends by hand).
    can_auto_send: bool = True

    def __init__(self, channel):
        self.channel = channel

    @abstractmethod
    def verify_webhook(self, headers, body: bytes) -> bool:
        """Return True if the inbound webhook is authentic. ``headers`` is case-insensitive."""

    @abstractmethod
    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        """Convert a provider payload into normalized inbound messages."""

    @abstractmethod
    def send_text(self, recipient_id: str, text: str) -> SendResult:
        """Send a text message to a contact."""

    def parse_events(self, payload: dict[str, Any]) -> list[ChannelEvent]:
        """Normalize every event in a payload. Default: one ``message`` event per message."""
        return [
            ChannelEvent(
                kind="message",
                event_name="message",
                dedupe_key=msg.message_id,
                user_id=msg.sender_id,
                message=msg,
            )
            for msg in self.parse_webhook(payload)
        ]
