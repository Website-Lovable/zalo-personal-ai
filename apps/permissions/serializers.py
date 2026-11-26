from rest_framework import serializers

from common.tenancy import TenantScopedSerializer

from .models import ApprovalQueue, PermissionRule


class PermissionRuleSerializer(TenantScopedSerializer):
    class Meta:
        model = PermissionRule
        fields = [
            "id",
            "name",
            "priority",
            "is_active",
            "contact_group",
            "relationship_type",
            "time_range_start",
            "time_range_end",
            "keyword_triggers",
            "action",
            "data_origin",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_keyword_triggers(self, value):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError("keyword_triggers phải là danh sách chuỗi.")
        return value

    def validate(self, attrs):
        start = attrs.get("time_range_start", getattr(self.instance, "time_range_start", None))
        end = attrs.get("time_range_end", getattr(self.instance, "time_range_end", None))
        if (start is None) != (end is None):
            raise serializers.ValidationError("Cần cả giờ bắt đầu và giờ kết thúc.")
        return attrs


class ApprovalSerializer(serializers.ModelSerializer):
    message_text = serializers.CharField(source="message.content", read_only=True, default="")
    contact_name = serializers.CharField(source="contact.display_name", read_only=True, default="")

    class Meta:
        model = ApprovalQueue
        fields = [
            "id",
            "source",
            "kind",
            "message",
            "message_text",
            "conversation",
            "contact",
            "contact_name",
            "reasons",
            "proposed_response",
            "ai_confidence",
            "status",
            "owner_staff_ref",
            "reviewed_by",
            "reviewed_at",
            "modified_response",
            "expires_at",
            "sent_message",
            "send_error",
            "data_origin",
            "created_at",
        ]
        read_only_fields = fields


class ApprovalDecisionSerializer(serializers.Serializer):
    modified_response = serializers.CharField(required=False, allow_blank=False)
    reason = serializers.CharField(required=False, allow_blank=True)
