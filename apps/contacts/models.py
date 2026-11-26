from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from common.models import TenantScopedModel


class ReplyPermission(models.TextChoices):
    AUTO_REPLY = "auto_reply", "Tự động trả lời"
    NEED_APPROVAL = "need_approval", "Cần duyệt"
    DO_NOT_REPLY = "do_not_reply", "Không trả lời"


class ConsentStatus(models.TextChoices):
    UNKNOWN = "unknown", "Chưa rõ"
    GRANTED = "granted", "Đồng ý liên hệ"
    WITHDRAWN = "withdrawn", "Đã rút đồng ý"


class ConsentSource(models.TextChoices):
    NONE = "", "—"
    ZALO_INBOUND = "zalo_inbound", "Khách chủ động nhắn"
    CRS = "crs", "Đồng bộ từ CRS"
    FORM = "form", "Biểu mẫu đăng ký"
    MANUAL = "manual", "Nhập tay (có bằng chứng)"


class Contact(TenantScopedModel):
    """A person on Zalo. CRS is the source of truth; this is a conversation copy."""

    class RelationshipType(models.TextChoices):
        FAMILY = "family", "Gia đình"
        FRIEND = "friend", "Bạn bè"
        COLLEAGUE = "colleague", "Đồng nghiệp"
        CUSTOMER = "customer", "Khách hàng"
        PARTNER = "partner", "Đối tác"
        ACQUAINTANCE = "acquaintance", "Người quen"
        UNKNOWN = "unknown", "Chưa rõ"

    class Segment(models.TextChoices):
        B2B = "b2b", "B2B"
        B2C = "b2c", "B2C"
        UNKNOWN = "unknown", "Chưa rõ"

    channel = models.ForeignKey(
        "zalo_channels.Channel", on_delete=models.CASCADE, related_name="contacts"
    )
    zalo_user_id = models.CharField(max_length=100)
    display_name = models.CharField(max_length=255)
    avatar_url = models.URLField(blank=True)
    relationship_type = models.CharField(
        max_length=20, choices=RelationshipType.choices, default=RelationshipType.UNKNOWN
    )
    relationship_notes = models.TextField(blank=True)
    reply_permission = models.CharField(
        max_length=20, choices=ReplyPermission.choices, default=ReplyPermission.NEED_APPROVAL
    )
    # Consent (Kickoff 2.1)
    consent_status = models.CharField(
        max_length=20, choices=ConsentStatus.choices, default=ConsentStatus.UNKNOWN
    )
    consent_source = models.CharField(
        max_length=20, choices=ConsentSource.choices, blank=True, default=ConsentSource.NONE
    )
    consent_at = models.DateTimeField(null=True, blank=True)
    # CRS link (copy only; CRS wins on conflict)
    crs_customer_id = models.CharField(max_length=100, blank=True, db_index=True)
    segment = models.CharField(max_length=10, choices=Segment.choices, default=Segment.UNKNOWN)
    owner_staff_ref = models.CharField(max_length=100, blank=True)
    # True once the contact has messaged us first (personal channel may only reply then).
    has_messaged_first = models.BooleanField(default=False)
    # Zalo OA follower state (null = unknown, e.g. Bot or never synced).
    is_follower = models.BooleanField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "channel", "zalo_user_id"], name="uniq_contact_per_channel"
            ),
        ]

    def __str__(self):
        return self.display_name

    @property
    def has_consent(self) -> bool:
        return self.consent_status == ConsentStatus.GRANTED

    @property
    def has_documented_consent(self) -> bool:
        """Consent we can show proof of: granted, with a source and a time (Kickoff 2.1)."""
        return self.has_consent and bool(self.consent_source) and self.consent_at is not None

    def can_contact_proactively(self) -> bool:
        """Outbound messages the contact did not ask for need explicit consent."""
        return self.has_consent and not self.channel.is_personal

    def can_receive_reply(self) -> bool:
        if self.reply_permission == ReplyPermission.DO_NOT_REPLY:
            return False
        if self.consent_status == ConsentStatus.WITHDRAWN:
            return False
        if self.channel.is_personal and not self.has_messaged_first:
            return False
        return True

    def clean(self):
        super().clean()
        if self.channel_id and self.tenant_id and self.channel.tenant_id != self.tenant_id:
            raise ValidationError({"channel": "Kênh không thuộc tenant này."})
        if self.consent_status == ConsentStatus.GRANTED and not self.consent_source:
            raise ValidationError({"consent_source": "Cần ghi nguồn đồng ý."})

    def set_consent(self, status: str, source: str = "", at=None):
        self.consent_status = status
        self.consent_source = (
            source if status == ConsentStatus.GRANTED else (source or self.consent_source)
        )
        self.consent_at = at or timezone.now()


class ContactGroup(TenantScopedModel):
    name = models.CharField(max_length=255)
    contacts = models.ManyToManyField(Contact, blank=True, related_name="groups")
    default_permission = models.CharField(
        max_length=20, choices=ReplyPermission.choices, default=ReplyPermission.NEED_APPROVAL
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
