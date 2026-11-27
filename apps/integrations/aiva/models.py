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


class AIVALiveSwitch(models.Model):
    """Platform-wide runtime switch for live exchange with AIVA (Phase 6), one row (pk=1).

    ``paused`` stops every live exchange at once without a redeploy (rollback level 2).
    ``last_seen_mode`` lets us write an audit entry whenever AIVA_INTEGRATION_MODE changes.
    """

    paused = models.BooleanField(default=False)
    reason = models.TextField(blank=True)
    changed_by = models.CharField(max_length=150, blank=True)
    changed_at = models.DateTimeField(null=True, blank=True)
    last_seen_mode = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"AIVALiveSwitch(paused={self.paused})"


class AIVALiveEnrollment(models.Model):
    """Per-tenant permission to exchange REAL data with AIVA (Phase 6).

    Two people sign off: the tenant owner acknowledges in the app (``acknowledged``), then
    the platform operator activates after a passing preflight, quoting the director's
    written approval (``active``). Either side can stop it again: ``paused`` keeps the queue
    frozen for a later resume, ``revoked`` withdraws the owner's acknowledgement and drops
    events not yet sent.
    """

    class Status(models.TextChoices):
        ACKNOWLEDGED = "acknowledged", "Chủ tài khoản đã xác nhận"
        ACTIVE = "active", "Đang kết nối dữ liệu thật"
        PAUSED = "paused", "Tạm dừng"
        REVOKED = "revoked", "Đã thu hồi"

    tenant = models.OneToOneField(
        "accounts.Tenant", on_delete=models.CASCADE, related_name="aiva_live_enrollment"
    )
    status = models.CharField(max_length=15, choices=Status.choices, db_index=True)
    acknowledged_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledgement_version = models.CharField(max_length=10, blank=True)
    acknowledgement_text = models.TextField(blank=True)
    approval_ref = models.CharField(max_length=255, blank=True)
    activated_by = models.CharField(max_length=150, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_by = models.CharField(max_length=150, blank=True)
    pause_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AIVALiveEnrollment({self.tenant_id}, {self.status})"
