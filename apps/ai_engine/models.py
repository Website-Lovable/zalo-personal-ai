from __future__ import annotations

from django.db import models

from common.models import DataOriginMixin, TenantQuerySet, TimestampMixin


class OwnerProfile(TimestampMixin, DataOriginMixin):
    """Owner intelligence profile (Blueprint B3 #1)."""

    tenant = models.OneToOneField(
        "accounts.Tenant", on_delete=models.CASCADE, related_name="owner_profile"
    )
    occupation = models.CharField(max_length=255, blank=True)
    expertise_areas = models.JSONField(default=list, blank=True)
    education = models.TextField(blank=True)
    personality_traits = models.JSONField(default=list, blank=True)
    values_and_beliefs = models.TextField(blank=True)
    communication_preferences = models.JSONField(default=dict, blank=True)
    custom_knowledge = models.TextField(blank=True)
    topics_to_avoid = models.JSONField(default=list, blank=True)

    objects = TenantQuerySet.as_manager()


class CommunicationStyle(TimestampMixin, DataOriginMixin):
    """Owner communication style (Blueprint B3 #3)."""

    class EmojiUsage(models.TextChoices):
        NEVER = "never", "Không dùng"
        RARELY = "rarely", "Hiếm khi"
        SOMETIMES = "sometimes", "Thỉnh thoảng"
        OFTEN = "often", "Thường xuyên"

    tenant = models.OneToOneField(
        "accounts.Tenant", on_delete=models.CASCADE, related_name="communication_style"
    )
    formality_level = models.PositiveSmallIntegerField(default=5)  # 1 casual .. 10 formal
    emoji_usage = models.CharField(
        max_length=10, choices=EmojiUsage.choices, default=EmojiUsage.SOMETIMES
    )
    typical_greeting = models.CharField(max_length=255, blank=True)
    typical_farewell = models.CharField(max_length=255, blank=True)
    language_patterns = models.JSONField(default=dict, blank=True)
    sample_messages = models.JSONField(default=list, blank=True)
    tone_by_relationship = models.JSONField(default=dict, blank=True)

    objects = TenantQuerySet.as_manager()


class LLMUsage(models.Model):
    """Token usage per tenant and call (billing for subscription tiers)."""

    tenant = models.ForeignKey("accounts.Tenant", on_delete=models.CASCADE, related_name="+")
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=100)
    purpose = models.CharField(max_length=50, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cache_read_input_tokens = models.PositiveIntegerField(default=0)
    cache_creation_input_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.model} {self.input_tokens}/{self.output_tokens}"


class AIControl(TimestampMixin):
    """Per-tenant AI switches: the emergency stop (Blueprint C6 #7) and reply policy."""

    tenant = models.OneToOneField(
        "accounts.Tenant", on_delete=models.CASCADE, related_name="ai_control"
    )
    emergency_stop = models.BooleanField(default=False)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stopped_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    stop_reason = models.CharField(max_length=500, blank=True)
    resumed_at = models.DateTimeField(null=True, blank=True)
    # If the contact sincerely asks whether they talk to a bot, the assistant says it is the
    # owner's assistant. Kept on by default: denying it would deceive the contact.
    disclose_ai_when_asked = models.BooleanField(default=True)

    objects = TenantQuerySet.as_manager()

    def __str__(self):
        return f"AIControl({self.tenant_id}, stop={self.emergency_stop})"

    @classmethod
    def for_tenant(cls, tenant) -> AIControl:
        obj, _ = cls.objects.get_or_create(tenant=tenant)
        return obj


class AIReplyLog(TimestampMixin, DataOriginMixin):
    """One AI pipeline run for one inbound message: what was decided and why."""

    class Outcome(models.TextChoices):
        SENT = "sent", "Đã tự gửi"
        QUEUED_FOR_OWNER = "queued_for_owner", "Chờ chủ gửi"
        NEED_APPROVAL = "need_approval", "Chờ duyệt"
        ESCALATED = "escalated", "Chuyển người xử lý"
        NOT_REPLIED = "not_replied", "Không trả lời"
        SKIPPED = "skipped", "Bỏ qua"
        FAILED = "failed", "Lỗi"

    tenant = models.ForeignKey("accounts.Tenant", on_delete=models.CASCADE, related_name="+")
    conversation = models.ForeignKey(
        "conversations.Conversation", on_delete=models.CASCADE, related_name="ai_runs"
    )
    inbound_message = models.OneToOneField(
        "conversations.Message", on_delete=models.CASCADE, related_name="ai_run"
    )
    outcome = models.CharField(max_length=20, choices=Outcome.choices, db_index=True)
    decision = models.CharField(max_length=20, blank=True)  # final permission action
    reasons = models.JSONField(default=list, blank=True)  # machine codes, e.g. sensitive:gia
    matched_rule = models.ForeignKey(
        "permissions.PermissionRule",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    model = models.CharField(max_length=100, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    draft = models.TextField(blank=True)
    error = models.CharField(max_length=500, blank=True)
    output_message = models.ForeignKey(
        "conversations.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    approval = models.ForeignKey(
        "permissions.ApprovalQueue",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    objects = TenantQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
