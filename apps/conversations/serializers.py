from rest_framework import serializers

from common.tenancy import TenantScopedSerializer

from .models import Conversation, Message, PendingOwnerSend


class ConversationSerializer(TenantScopedSerializer):
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)

    class Meta:
        model = Conversation
        fields = [
            "id",
            "channel",
            "contact",
            "contact_name",
            "status",
            "last_message_at",
            "ai_enabled",
            "human_takeover",
            "data_origin",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "last_message_at", "created_at", "updated_at"]

    def validate(self, attrs):
        channel = attrs.get("channel", getattr(self.instance, "channel", None))
        contact = attrs.get("contact", getattr(self.instance, "contact", None))
        if channel and contact and contact.channel_id != channel.pk:
            raise serializers.ValidationError({"contact": "Liên hệ không thuộc kênh này."})
        return attrs


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "direction",
            "content",
            "message_type",
            "is_ai_generated",
            "ai_confidence",
            "was_approved",
            "approved_by",
            "zalo_message_id",
            "zalo_timestamp",
            "data_origin",
            "created_at",
        ]
        read_only_fields = fields


class PendingOwnerSendSerializer(serializers.ModelSerializer):
    class Meta:
        model = PendingOwnerSend
        fields = [
            "id",
            "channel",
            "conversation",
            "reply_to",
            "draft_text",
            "status",
            "handled_by",
            "handled_at",
            "data_origin",
            "created_at",
        ]
        read_only_fields = fields
