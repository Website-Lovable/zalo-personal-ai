from django.contrib.auth.models import AbstractUser
from django.db import models

from common.models import TimestampMixin


class Tenant(TimestampMixin):
    """One tenant = one account owner using the service (Blueprint C2)."""

    class Tier(models.TextChoices):
        BASIC = "basic", "Basic"
        PRO = "pro", "Pro"
        ENTERPRISE = "enterprise", "Enterprise"

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    subscription_tier = models.CharField(max_length=20, choices=Tier.choices, default=Tier.BASIC)
    is_active = models.BooleanField(default=True)
    settings = models.JSONField(default=dict, blank=True)
    # Mapping to the tenant id on the AIVA / CRS side (Kickoff 2.1).
    external_ref = models.CharField(max_length=100, blank=True, db_index=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = "owner", "Chủ tài khoản"
        ADMIN = "admin", "Quản trị"
        VIEWER = "viewer", "Chỉ xem"

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name="users"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    phone = models.CharField(max_length=20, blank=True)

    def has_role(self, minimum: str) -> bool:
        from .rbac import role_at_least

        return role_at_least(self.role, minimum)
