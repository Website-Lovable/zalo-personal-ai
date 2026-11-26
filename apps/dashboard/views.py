from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.rbac import IsTenantMember
from apps.channels.models import Channel
from apps.contacts.models import Contact
from apps.conversations.models import Conversation, PendingOwnerSend
from apps.permissions.models import ApprovalQueue
from common.tenancy import get_request_tenant


class StatsView(APIView):
    permission_classes = [IsTenantMember]

    @extend_schema(
        responses=inline_serializer(
            "DashboardStats",
            {
                name: serializers.IntegerField()
                for name in [
                    "channels",
                    "contacts",
                    "contacts_with_consent",
                    "conversations",
                    "pending_approvals",
                    "pending_owner_sends",
                ]
            },
        )
    )
    def get(self, request):
        tenant = get_request_tenant(request)
        return Response(
            {
                "channels": Channel.objects.for_tenant(tenant).count(),
                "contacts": Contact.objects.for_tenant(tenant).count(),
                "contacts_with_consent": Contact.objects.for_tenant(tenant)
                .filter(consent_status="granted")
                .count(),
                "conversations": Conversation.objects.for_tenant(tenant).count(),
                "pending_approvals": ApprovalQueue.objects.for_tenant(tenant)
                .filter(status="pending")
                .count(),
                "pending_owner_sends": PendingOwnerSend.objects.for_tenant(tenant)
                .filter(status="pending")
                .count(),
            }
        )
