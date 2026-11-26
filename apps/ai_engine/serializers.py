from rest_framework import serializers

from .models import AIControl, AIReplyLog, CommunicationStyle, OwnerProfile


class OwnerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = OwnerProfile
        exclude = ["tenant"]
        read_only_fields = ["id", "created_at", "updated_at"]


class CommunicationStyleSerializer(serializers.ModelSerializer):
    formality_level = serializers.IntegerField(min_value=1, max_value=10, required=False)

    class Meta:
        model = CommunicationStyle
        exclude = ["tenant"]
        read_only_fields = ["id", "created_at", "updated_at"]


class AIControlSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIControl
        fields = [
            "emergency_stop",
            "stopped_at",
            "stopped_by",
            "stop_reason",
            "resumed_at",
            "disclose_ai_when_asked",
            "updated_at",
        ]
        read_only_fields = [
            "emergency_stop",
            "stopped_at",
            "stopped_by",
            "stop_reason",
            "resumed_at",
            "updated_at",
        ]


class EmergencyStopSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class AIReplyLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIReplyLog
        fields = [
            "id",
            "conversation",
            "inbound_message",
            "outcome",
            "decision",
            "reasons",
            "matched_rule",
            "model",
            "confidence",
            "draft",
            "error",
            "output_message",
            "approval",
            "data_origin",
            "created_at",
        ]
        read_only_fields = fields


class PreviewRequestSerializer(serializers.Serializer):
    conversation = serializers.IntegerField()


class PreviewResponseSerializer(serializers.Serializer):
    action = serializers.CharField()
    skip = serializers.BooleanField()
    reasons = serializers.ListField(child=serializers.CharField())
    draft = serializers.CharField(allow_blank=True)
    confidence = serializers.FloatField()
    model = serializers.CharField(allow_blank=True)
