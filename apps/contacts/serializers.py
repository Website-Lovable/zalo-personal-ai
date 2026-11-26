from django.utils import timezone
from rest_framework import serializers

from common.tenancy import TenantScopedSerializer

from .models import ConsentStatus, Contact, ContactGroup


class ContactSerializer(TenantScopedSerializer):
    has_consent = serializers.BooleanField(read_only=True)

    class Meta:
        model = Contact
        fields = [
            "id",
            "channel",
            "zalo_user_id",
            "display_name",
            "avatar_url",
            "relationship_type",
            "relationship_notes",
            "reply_permission",
            "consent_status",
            "consent_source",
            "consent_at",
            "has_consent",
            "crs_customer_id",
            "segment",
            "owner_staff_ref",
            "has_messaged_first",
            "data_origin",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "has_messaged_first", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = self.instance
        status = attrs.get(
            "consent_status", getattr(instance, "consent_status", ConsentStatus.UNKNOWN)
        )
        source = attrs.get("consent_source", getattr(instance, "consent_source", ""))
        status_changed = instance is None or status != instance.consent_status
        if status == ConsentStatus.GRANTED and not source:
            raise serializers.ValidationError(
                {"consent_source": "Cần ghi nguồn đồng ý (consent_source)."}
            )
        if status_changed and status != ConsentStatus.UNKNOWN and not attrs.get("consent_at"):
            attrs["consent_at"] = timezone.now()
        if instance is not None and "channel" in attrs and attrs["channel"] != instance.channel:
            raise serializers.ValidationError({"channel": "Không chuyển liên hệ sang kênh khác."})
        return attrs


class ContactGroupSerializer(TenantScopedSerializer):
    class Meta:
        model = ContactGroup
        fields = [
            "id",
            "name",
            "contacts",
            "default_permission",
            "data_origin",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
