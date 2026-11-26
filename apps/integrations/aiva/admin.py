from django.contrib import admin

from .models import IntegrationAuditLog, IntegrationOutbox


@admin.register(IntegrationOutbox)
class IntegrationOutboxAdmin(admin.ModelAdmin):
    list_display = ["event_type", "tenant", "status", "attempts", "data_origin", "created_at"]
    list_filter = ["status", "event_type", "data_origin"]


@admin.register(IntegrationAuditLog)
class IntegrationAuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "tenant", "direction", "mode", "event_type", "success"]
    list_filter = ["direction", "mode", "success"]
