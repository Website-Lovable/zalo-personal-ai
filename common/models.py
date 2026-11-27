"""Shared base models: timestamps, data origin (demo|real), tenant scoping, audit log."""

from django.db import models


class TimestampMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DataOrigin(models.TextChoices):
    DEMO = "demo", "Demo"
    REAL = "real", "Real"


class DataOriginMixin(models.Model):
    """Every business record says whether it is demo (seed/sandbox) or real data."""

    data_origin = models.CharField(
        max_length=10, choices=DataOrigin.choices, default=DataOrigin.REAL, db_index=True
    )

    class Meta:
        abstract = True


class TenantQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        if tenant is None:
            return self.none()
        return self.filter(tenant=tenant)


class TenantScopedModel(TimestampMixin, DataOriginMixin):
    """Base for tenant-owned business records."""

    tenant = models.ForeignKey("accounts.Tenant", on_delete=models.CASCADE, related_name="+")

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Audit trail of significant actions (Blueprint C2, Kickoff rule 7)."""

    tenant = models.ForeignKey(
        "accounts.Tenant", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=100, db_index=True)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["tenant", "action"])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.model_name}#{self.object_id}"


class OpsDailySnapshot(models.Model):
    """Measured daily operating figures, platform-wide (``common.ops_report``).

    Counts only: no tenant names, no customer data, no message content. Recomputed every hour
    for today and yesterday, so the latest row is always current and yesterday ends final.
    """

    date = models.DateField(unique=True)
    data = models.JSONField(default=dict)
    computed_at = models.DateTimeField()
    pushed_at = models.DateTimeField(null=True, blank=True)
    push_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"OpsDailySnapshot({self.date})"
