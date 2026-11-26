import uuid

from django.db import models

from common.models import TenantScopedModel


class IntegrationOutbox(TenantScopedModel):
    """Transactional outbox for Zalo AI -> AIVA webhooks (worker + retry in Phase 3.5)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Chờ gửi"
        SENT = "sent", "Đã gửi"
        FAILED = "failed", "Lỗi, sẽ thử lại"
        DEAD = "dead", "Bỏ (quá số lần thử)"

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=50, db_index=True)
    schema_version = models.CharField(max_length=10, default="1.0")
    payload = models.JSONField()  # full v1 envelope
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class IntegrationAuditLog(models.Model):
    """Every exchange (or blocked attempt) with AIVA, in both directions."""

    class Direction(models.TextChoices):
        OUTBOUND = "outbound", "Zalo AI -> AIVA"
        INBOUND = "inbound", "AIVA -> Zalo AI"

    tenant = models.ForeignKey(
        "accounts.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    direction = models.CharField(max_length=10, choices=Direction.choices)
    mode = models.CharField(max_length=10)
    event_type = models.CharField(max_length=50, blank=True)
    event_id = models.UUIDField(null=True, blank=True)
    data_origin = models.CharField(max_length=10, blank=True)
    success = models.BooleanField(default=False)
    status_code = models.PositiveIntegerField(null=True, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.direction} {self.event_type} ok={self.success}"


class IntegrationInboundRequest(models.Model):
    """Idempotency record for write requests from AIVA (``Idempotency-Key`` header).

    A repeated key with the same body returns the stored response; with another body it is
    refused (409). Together with the signed timestamp this stops replays of signed POSTs.
    """

    tenant = models.ForeignKey("accounts.Tenant", on_delete=models.CASCADE, related_name="+")
    idempotency_key = models.CharField(max_length=100)
    endpoint = models.CharField(max_length=100)
    body_sha256 = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"], name="uniq_aiva_inbound_idempotency"
            )
        ]

    def __str__(self):
        return f"{self.endpoint} {self.idempotency_key}"
