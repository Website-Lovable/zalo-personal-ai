from django.core.exceptions import ValidationError
from django.db import models

from common.fields import EncryptedTextField
from common.models import TenantScopedModel

# Text the tenant owner must accept before a zalo_personal channel may run in `auto` mode
# (Blueprint B2 #2, Kickoff 3 #2). Bump the version when the wording changes.
RISK_STATEMENT_VERSION = "v1"
RISK_STATEMENT_TEXT = (
    "Tôi là chủ tài khoản Zalo cá nhân được kết nối và tự thực hiện việc đăng nhập. "
    "Tôi hiểu rằng Điều khoản sử dụng của Zalo cấm đăng nhập/sử dụng dịch vụ qua phần mềm "
    "bên thứ ba chưa được Zalo chấp thuận; tài khoản có thể bị hạn chế hoặc khóa, và phương "
    "thức này có thể ngừng hoạt động bất cứ lúc nào. Tôi chấp nhận rủi ro này. Trợ lý chỉ trả "
    "lời người đã nhắn trước, không gửi hàng loạt, có giới hạn tốc độ và công tắc dừng khẩn cấp."
)


class ChannelType(models.TextChoices):
    ZALO_OA = "zalo_oa", "Zalo OA"
    ZALO_BOT = "zalo_bot", "Zalo Bot"
    ZALO_PERSONAL = "zalo_personal", "Zalo cá nhân"


class ChannelMode(models.TextChoices):
    AUTO = "auto", "Tự động gửi"
    ASSIST = "assist", "Soạn sẵn, chủ tự gửi"


class Channel(TenantScopedModel):
    """One connected Zalo account (OA, Bot or personal). A tenant can have many."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Hoạt động"
        PAUSED = "paused", "Tạm dừng"
        RESTRICTED = "restricted", "Bị hạn chế"
        DISCONNECTED = "disconnected", "Chưa kết nối"

    channel_type = models.CharField(max_length=20, choices=ChannelType.choices)
    mode = models.CharField(max_length=10, choices=ChannelMode.choices, default=ChannelMode.AUTO)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DISCONNECTED)
    config = models.JSONField(default=dict, blank=True)  # non-secret settings only
    zalo_oa_id = models.CharField(max_length=100, blank=True)
    zalo_app_id = models.CharField(max_length=100, blank=True)
    access_token = EncryptedTextField(blank=True)
    refresh_token = EncryptedTextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    # Login session for zalo_personal (Phase 2b). Always encrypted.
    session_data = EncryptedTextField(blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "channel_type"])]

    def __str__(self):
        return f"{self.name} ({self.channel_type})"

    @property
    def is_personal(self) -> bool:
        return self.channel_type == ChannelType.ZALO_PERSONAL

    @property
    def risk_acknowledged(self) -> bool:
        if not self.pk:
            return False
        return self.risk_acknowledgements.filter(
            revoked_at__isnull=True, statement_version=RISK_STATEMENT_VERSION
        ).exists()

    def clean(self):
        super().clean()
        if self.is_personal and self.mode == ChannelMode.AUTO and not self.risk_acknowledged:
            raise ValidationError(
                {"mode": "Kênh Zalo cá nhân chỉ bật 'auto' sau khi chủ tenant xác nhận rủi ro."}
            )

    def save(self, *args, **kwargs):
        # Safe default: a new personal channel always starts in assist mode.
        if self.is_personal and not self.pk:
            self.mode = ChannelMode.ASSIST
        if self.is_personal and self.mode == ChannelMode.AUTO and not self.risk_acknowledged:
            raise ValidationError("zalo_personal auto mode requires a risk acknowledgement")
        super().save(*args, **kwargs)


class ChannelRiskAcknowledgement(TenantScopedModel):
    """Written risk acceptance by the tenant owner for a zalo_personal channel."""

    channel = models.ForeignKey(
        Channel, on_delete=models.CASCADE, related_name="risk_acknowledgements"
    )
    acknowledged_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    full_name = models.CharField(max_length=255)
    statement_version = models.CharField(max_length=20, default=RISK_STATEMENT_VERSION)
    statement_text = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-acknowledged_at"]
