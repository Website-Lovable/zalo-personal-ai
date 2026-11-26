from rest_framework import serializers

from common.tenancy import TenantScopedSerializer

from .models import RISK_STATEMENT_TEXT, RISK_STATEMENT_VERSION, Channel, ChannelMode


class ChannelSerializer(TenantScopedSerializer):
    access_token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    refresh_token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    webhook_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_access_token = serializers.SerializerMethodField()
    risk_acknowledged = serializers.BooleanField(read_only=True)

    class Meta:
        model = Channel
        fields = [
            "id",
            "channel_type",
            "mode",
            "name",
            "is_active",
            "status",
            "config",
            "zalo_oa_id",
            "zalo_app_id",
            "access_token",
            "refresh_token",
            "webhook_secret",
            "has_access_token",
            "token_expires_at",
            "risk_acknowledged",
            "last_error",
            "connected_at",
            "data_origin",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "token_expires_at",
            "last_error",
            "connected_at",
            "created_at",
            "updated_at",
        ]

    def get_has_access_token(self, obj) -> bool:
        return bool(obj.access_token)

    def validate_config(self, value):
        lowered = {k.lower() for k in value}
        if any(word in key for key in lowered for word in ("secret", "token", "password")):
            raise serializers.ValidationError(
                "Không lưu secret trong config; dùng trường token được mã hóa."
            )
        return value

    def validate(self, attrs):
        channel_type = attrs.get("channel_type", getattr(self.instance, "channel_type", None))
        if (
            self.instance
            and "channel_type" in attrs
            and (attrs["channel_type"] != self.instance.channel_type)
        ):
            raise serializers.ValidationError({"channel_type": "Không đổi loại kênh."})
        mode = attrs.get("mode")
        if channel_type == "zalo_personal" and mode == ChannelMode.AUTO:
            acknowledged = self.instance.risk_acknowledged if self.instance else False
            if not acknowledged:
                raise serializers.ValidationError(
                    {"mode": "Cần chủ tenant xác nhận rủi ro trước khi bật chế độ auto."}
                )
        return attrs


class RiskStatementSerializer(serializers.Serializer):
    statement_version = serializers.CharField(read_only=True, default=RISK_STATEMENT_VERSION)
    statement_text = serializers.CharField(read_only=True, default=RISK_STATEMENT_TEXT)


class RiskAcknowledgementSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    statement_version = serializers.CharField(max_length=20)
    accepted = serializers.BooleanField()

    def validate_accepted(self, value):
        if value is not True:
            raise serializers.ValidationError("Phải đồng ý với nội dung xác nhận rủi ro.")
        return value

    def validate_statement_version(self, value):
        if value != RISK_STATEMENT_VERSION:
            raise serializers.ValidationError("Phiên bản nội dung xác nhận không còn hiệu lực.")
        return value


class OAuthStartSerializer(serializers.Serializer):
    authorize_url = serializers.URLField(read_only=True)


class BotConnectSerializer(serializers.Serializer):
    bot_token = serializers.CharField(write_only=True, max_length=512)
    delivery = serializers.ChoiceField(choices=["webhook", "polling"], default="webhook")


class WebhookInfoSerializer(serializers.Serializer):
    webhook_url = serializers.CharField(read_only=True)
    oauth_redirect_uri = serializers.CharField(read_only=True)
    delivery = serializers.CharField(read_only=True)
    backend = serializers.CharField(read_only=True)


class TaskQueuedSerializer(serializers.Serializer):
    status = serializers.CharField(read_only=True)
