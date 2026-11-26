from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["timestamp", "tenant", "user", "action", "model_name", "object_id"]
    list_filter = ["action"]
    readonly_fields = [f.name for f in AuditLog._meta.fields]
