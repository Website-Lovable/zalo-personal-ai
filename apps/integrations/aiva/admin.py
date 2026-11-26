from django.contrib import admin

from .models import (
    AIVALiveEnrollment,
    AIVALiveSwitch,
    IntegrationAuditLog,
    IntegrationOutbox,
)


@admin.register(IntegrationOutbox)
class IntegrationOutboxAdmin(admin.ModelAdmin):
    list_display = ["event_type", "tenant", "status", "attempts", "data_origin", "created_at"]
    list_filter = ["status", "event_type", "data_origin"]


@admin.register(IntegrationAuditLog)
class IntegrationAuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "tenant", "direction", "mode", "event_type", "success"]
    list_filter = ["direction", "mode", "success"]


@admin.register(AIVALiveEnrollment)
class AIVALiveEnrollmentAdmin(admin.ModelAdmin):
    """Read-only: state changes go through ``manage.py aiva_live`` or the app (audited)."""

    list_display = ["tenant", "status", "acknowledged_at", "activated_at", "paused_at"]
    list_filter = ["status"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AIVALiveSwitch)
class AIVALiveSwitchAdmin(admin.ModelAdmin):
    list_display = ["paused", "reason", "changed_by", "changed_at", "last_seen_mode"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
