from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from apps.integrations.aiva import events as aiva_events
from apps.integrations.aiva.outbox import enqueue_event
from apps.memory.models import LongTermMemory
from apps.memory.serializers import LongTermMemorySerializer
from common.audit import log_action
from common.tenancy import TenantScopedViewSet

from .models import Contact, ContactGroup
from .serializers import ContactGroupSerializer, ContactSerializer


class ContactViewSet(TenantScopedViewSet):
    queryset = Contact.objects.select_related("channel")
    serializer_class = ContactSerializer
    filterset_fields = [
        "channel",
        "relationship_type",
        "reply_permission",
        "consent_status",
        "segment",
        "data_origin",
    ]
    audit_prefix = "contact"

    def perform_update(self, serializer):
        before = serializer.instance.consent_status
        super().perform_update(serializer)
        contact = serializer.instance
        if contact.consent_status != before:
            log_action(
                "contact.consent_changed",
                obj=contact,
                request=self.request,
                changes={
                    "from": before,
                    "to": contact.consent_status,
                    "source": contact.consent_source,
                    "at": contact.consent_at.isoformat() if contact.consent_at else None,
                },
            )
            enqueue_event(
                contact.tenant,
                "consent.changed",
                {
                    "contact_ref": str(contact.pk),
                    "crs_customer_id": contact.crs_customer_id,
                    "consent_status": contact.consent_status,
                    "consent_source": contact.consent_source,
                },
                data_origin=contact.data_origin,
                contact=contact,
            )
            aiva_events.lead_inbound(contact)  # a contact who wrote earlier just consented

    @extend_schema(responses=LongTermMemorySerializer(many=True))
    @action(detail=True, methods=["get"])
    def memory(self, request, pk=None):
        contact = self.get_object()
        memories = LongTermMemory.objects.for_tenant(self.get_tenant()).filter(contact=contact)
        page = self.paginate_queryset(memories)
        serializer = LongTermMemorySerializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class ContactGroupViewSet(TenantScopedViewSet):
    queryset = ContactGroup.objects.prefetch_related("contacts")
    serializer_class = ContactGroupSerializer
    filterset_fields = ["default_permission", "data_origin"]
    audit_prefix = "contact_group"
