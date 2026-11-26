from django.core.exceptions import ValidationError
from django.db import models

from common.models import TenantScopedModel


class RuleAction(models.TextChoices):
    AUTO_REPLY = "auto_reply", "Tự động trả lời"
    NEED_APPROVAL = "need_approval", "Cần duyệt"
    DO_NOT_REPLY = "do_not_reply", "Không trả lời"
    ESCALATE = "escalate", "Chuyển cấp trên"


class PermissionRule(TenantScopedModel):
    name = models.CharField(max_length=255)
    priority = models.IntegerField(default=0)  # higher = checked first
    is_active = models.BooleanField(default=True)
    contact_group = models.ForeignKey(
        "contacts.ContactGroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rules",
    )
    relationship_type = models.CharField(max_length=50, blank=True)
    time_range_start = models.TimeField(null=True, blank=True)
    time_range_end = models.TimeField(null=True, blank=True)
    keyword_triggers = models.JSONField(default=list, blank=True)
    action = models.CharField(max_length=20, choices=RuleAction.choices)

    class Meta:
        ordering = ["-priority", "id"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if bool(self.time_range_start) != bool(self.time_range_end):
            raise ValidationError("Cần cả giờ bắt đầu và giờ kết thúc.")


class ApprovalQueue(TenantScopedModel):
    """Drafts waiting for a human. Outreach drafted by Sales AI is approved by the Sales
    staff in charge of the customer (``owner_staff_ref`` from CRS, Kickoff 2.0 #2)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Chờ duyệt"
        APPROVED = "approved", "Đã duyệt"
        REJECTED = "rejected", "Từ chối"
        MODIFIED = "modified", "Duyệt có sửa"
        EXPIRED = "expired", "Hết hạn"

    class Source(models.TextChoices):
        INBOUND_REPLY = "inbound_reply", "Trả lời tin nhắn đến"
        AIVA_OUTREACH = "aiva_outreach", "Tin chăm sóc từ Sales AI"

    class Kind(models.TextChoices):
        DRAFT = "draft", "Bản nháp chờ duyệt"
        ESCALATION = "escalation", "Cần người xử lý"

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.INBOUND_REPLY)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.DRAFT)
    conversation = models.ForeignKey(
        "conversations.Conversation",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="approvals",
    )
    # Machine codes explaining why a human must decide (e.g. ["sensitive:gia"]).
    reasons = models.JSONField(default=list, blank=True)
    message = models.OneToOneField(
        "conversations.Message",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="approval",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="approvals",
    )
    proposed_response = models.TextField()
    ai_confidence = models.FloatField(default=0.0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    owner_staff_ref = models.CharField(max_length=100, blank=True, db_index=True)
    reviewed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    modified_response = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    sent_message = models.ForeignKey(
        "conversations.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    send_error = models.CharField(max_length=500, blank=True)

    @property
    def final_text(self) -> str:
        return self.modified_response or self.proposed_response

    class Meta:
        ordering = ["expires_at"]
