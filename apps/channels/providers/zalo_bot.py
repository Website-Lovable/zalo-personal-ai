"""Zalo Bot Platform provider (official API). Implemented in Phase 2."""

from common.exceptions import NotImplementedInPhase

from .base import ChannelProvider


class ZaloBotProvider(ChannelProvider):
    channel_type = "zalo_bot"

    def verify_webhook(self, headers, body):
        raise NotImplementedInPhase("Zalo Bot X-Bot-Api-Secret-Token check", "2")

    def parse_webhook(self, payload):
        raise NotImplementedInPhase("Zalo Bot update parsing", "2")

    def send_text(self, recipient_id, text):
        raise NotImplementedInPhase("Zalo Bot sendMessage", "2")
