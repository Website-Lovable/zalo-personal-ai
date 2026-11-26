from django.db import models

from common.models import TenantScopedModel


class ShortTermMemory(TenantScopedModel):
    """Context inside the current conversation; expires automatically."""

    conversation = models.ForeignKey(
        "conversations.Conversation", on_delete=models.CASCADE, related_name="short_term_memories"
    )
    context_summary = models.TextField()
    key_points = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField()


class LongTermMemory(TenantScopedModel):
    """Cross-session facts about a contact."""

    class MemoryType(models.TextChoices):
        FACT = "fact", "Sự kiện"
        PREFERENCE = "preference", "Sở thích"
        EVENT = "event", "Sự việc"
        RELATIONSHIP = "relationship", "Quan hệ"
        SUMMARY = "summary", "Tóm tắt"

    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.CASCADE, related_name="long_term_memories"
    )
    memory_type = models.CharField(
        max_length=20, choices=MemoryType.choices, default=MemoryType.FACT
    )
    content = models.TextField()
    importance = models.PositiveSmallIntegerField(default=5)  # 1-10
    source_conversation = models.ForeignKey(
        "conversations.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    last_accessed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-importance", "-created_at"]
