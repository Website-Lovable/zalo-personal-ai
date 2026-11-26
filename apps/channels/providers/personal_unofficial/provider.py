from common.exceptions import NotImplementedInPhase

from ..base import ChannelProvider


class PersonalUnofficialProvider(ChannelProvider):
    channel_type = "zalo_personal"

    def verify_webhook(self, headers, body):
        raise NotImplementedInPhase("Personal Zalo session events", "2b")

    def parse_webhook(self, payload):
        raise NotImplementedInPhase("Personal Zalo message parsing", "2b")

    def send_text(self, recipient_id, text):
        raise NotImplementedInPhase("Personal Zalo send (approved library adapter)", "2b")
