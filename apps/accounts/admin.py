from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Tenant, User


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "subscription_tier", "is_active", "external_ref"]
    search_fields = ["name", "slug", "external_ref"]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["username", "email", "tenant", "role", "is_active"]
    list_filter = ["role", "tenant"]
    fieldsets = (*BaseUserAdmin.fieldsets, ("Tenant", {"fields": ("tenant", "role", "phone")}))
