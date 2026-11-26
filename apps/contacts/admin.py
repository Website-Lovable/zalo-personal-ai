from django.contrib import admin

from .models import Contact, ContactGroup


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = [
        "display_name",
        "tenant",
        "channel",
        "relationship_type",
        "reply_permission",
        "consent_status",
        "data_origin",
    ]
    list_filter = ["relationship_type", "reply_permission", "consent_status", "data_origin"]
    search_fields = ["display_name", "zalo_user_id", "crs_customer_id"]


@admin.register(ContactGroup)
class ContactGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "tenant", "default_permission", "data_origin"]
