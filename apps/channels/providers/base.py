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
class SendResult:
    ok: bool
    message_id: str = ""
    error: str = ""


class ChannelProvider(ABC):
    channel_type: str = ""
    #: False for providers that can only draft (the owner sends by hand).
    can_auto_send: bool = True

    def __init__(self, channel):
        self.channel = channel

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Return True if the inbound webhook is authentic."""

    @abstractmethod
    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        """Convert a provider payload into normalized inbound messages."""

    @abstractmethod
    def send_text(self, recipient_id: str, text: str) -> SendResult:
        """Send a text message to a contact."""
