from django.contrib import admin

from .models import AIControl, AIReplyLog, CommunicationStyle, LLMUsage, OwnerProfile

admin.site.register(OwnerProfile)
admin.site.register(CommunicationStyle)
admin.site.register(LLMUsage)


@admin.register(AIControl)
class AIControlAdmin(admin.ModelAdmin):
    list_display = ("tenant", "emergency_stop", "stopped_at", "stopped_by")
    list_filter = ("emergency_stop",)


@admin.register(AIReplyLog)
class AIReplyLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "conversation", "outcome", "decision", "model")
    list_filter = ("outcome", "decision", "data_origin")
    readonly_fields = [f.name for f in AIReplyLog._meta.fields]
