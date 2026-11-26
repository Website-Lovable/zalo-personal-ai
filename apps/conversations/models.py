from django.db import models
from django.db.models import Q

from common.models import TenantScopedModel


class Conversation(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Đang hoạt động"
        PAUSED = "paused", "Tạm dừng"
        ARCHIVED = "archived", "Lưu trữ"

    channel = models.ForeignKey(
        "zalo_channels.Channel", on_delete=models.CASCADE, related_name="conversations"
    )
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.CASCADE, related_name="conversations"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    last_message_at = models.DateTimeField(null=True, blank=True)
    ai_enabled = models.BooleanField(default=True)
    # Human takeover (Blueprint C6 #8): owner handles the thread, AI stays silent.
    human_takeover = models.BooleanField(default=False)

    class Meta:
        ordering = ["-last_message_at", "-id"]

    def __str__(self):
        return f"Conversation #{self.pk} with {self.contact}"


class Message(TenantScopedModel):
    class Direction(models.TextChoices):
        INBOUND = "inbound", "Nhận"
        OUTBOUND = "outbound", "Gửi"

    class MessageType(models.TextChoices):
        TEXT = "text", "Văn bản"
        IMAGE = "image", "Ảnh"
        FILE = "file", "Tệp"
        STICKER = "sticker", "Sticker"
        LOCATION = "location", "Vị trí"
        AUDIO = "audio", "Âm thanh"
        VIDEO = "video", "Video"
        OTHER = "other", "Khác"

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    direction = models.CharField(max_length=10, choices=Direction.choices)
    content = models.TextField()
    message_type = models.CharField(
        max_length=10, choices=MessageType.choices, default=MessageType.TEXT
    )
    is_ai_generated = models.BooleanField(default=False)
    ai_confidence = models.FloatField(null=True, blank=True)
    was_approved = models.BooleanField(null=True, blank=True)  # null = auto
    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    zalo_message_id = models.CharField(max_length=100, blank=True)
    zalo_timestamp = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "zalo_message_id"],
                condition=~Q(zalo_message_id=""),
                name="uniq_zalo_message_per_conversation",
            ),
        ]


class PendingOwnerSend(TenantScopedModel):
    """'Chờ chủ gửi' inbox: drafts for channels in assist mode. The owner sends them by hand."""

    class Status(models.TextChoices):
        PENDING = "pending", "Chờ chủ gửi"
        SENT_BY_OWNER = "sent_by_owner", "Chủ đã gửi"
        DISMISSED = "dismissed", "Bỏ qua"

    channel = models.ForeignKey(
        "zalo_channels.Channel", on_delete=models.CASCADE, related_name="pending_owner_sends"
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="pending_owner_sends"
    )
    reply_to = models.ForeignKey(
        Message, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    draft_text = models.TextField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    handled_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
