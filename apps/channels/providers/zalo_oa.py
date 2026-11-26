"""Zalo Official Account provider (official API). Implemented in Phase 2."""

from common.exceptions import NotImplementedInPhase

from .base import ChannelProvider


class ZaloOAProvider(ChannelProvider):
    channel_type = "zalo_oa"

    def verify_webhook(self, headers, body):
        raise NotImplementedInPhase("Zalo OA webhook verification", "2")

    def parse_webhook(self, payload):
        raise NotImplementedInPhase("Zalo OA webhook parsing", "2")

    def send_text(self, recipient_id, text):
        raise NotImplementedInPhase("Zalo OA send message", "2")
