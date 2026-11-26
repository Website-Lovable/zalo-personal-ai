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
