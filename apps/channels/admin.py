from django.contrib import admin

from .models import Channel, ChannelRiskAcknowledgement


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ["name", "tenant", "channel_type", "mode", "status", "is_active", "data_origin"]
    list_filter = ["channel_type", "mode", "status", "data_origin"]
    exclude = ["access_token", "refresh_token", "session_data"]


@admin.register(ChannelRiskAcknowledgement)
class ChannelRiskAcknowledgementAdmin(admin.ModelAdmin):
    list_display = ["channel", "tenant", "acknowledged_by", "statement_version", "acknowledged_at"]
    readonly_fields = [f.name for f in ChannelRiskAcknowledgement._meta.fields]
