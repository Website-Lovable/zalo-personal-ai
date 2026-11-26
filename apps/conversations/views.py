from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.rbac import IsTenantMember, RolePermission
from common.audit import log_action
from common.tenancy import TenantScopedViewSet, get_request_tenant

from .models import Conversation, Message, PendingOwnerSend
from .serializers import ConversationSerializer, MessageSerializer, PendingOwnerSendSerializer


class ConversationViewSet(TenantScopedViewSet):
    queryset = Conversation.objects.select_related("contact", "channel")
    serializer_class = ConversationSerializer
    filterset_fields = [
        "channel",
        "contact",
        "status",
        "ai_enabled",
        "human_takeover",
        "data_origin",
    ]
    audit_prefix = "conversation"

    @extend_schema(responses=MessageSerializer(many=True))
    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        qs = Message.objects.for_tenant(self.get_tenant()).filter(conversation=conversation)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(MessageSerializer(page, many=True).data)


class PendingOwnerSendViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """'Chờ chủ gửi' inbox for assist-mode channels."""

    serializer_class = PendingOwnerSendSerializer
    permission_classes = [IsTenantMember, RolePermission]
    filterset_fields = ["status", "channel", "conversation"]
    queryset = PendingOwnerSend.objects.all()

    def get_queryset(self):
        return super().get_queryset().filter(tenant=get_request_tenant(self.request))

    def _handle(self, request, new_status, audit_action):
        item = self.get_object()
        if item.status != PendingOwnerSend.Status.PENDING:
            raise ValidationError({"status": "Mục này đã được xử lý."})
        item.status = new_status
        item.handled_by = request.user
        item.handled_at = timezone.now()
        item.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
        log_action(audit_action, obj=item, request=request)
        return Response(self.get_serializer(item).data)

    @extend_schema(request=None, responses=PendingOwnerSendSerializer)
    @action(detail=True, methods=["post"], url_path="mark-sent")
    def mark_sent(self, request, pk=None):
        return self._handle(request, PendingOwnerSend.Status.SENT_BY_OWNER, "owner_send.sent")

    @extend_schema(request=None, responses=PendingOwnerSendSerializer)
    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        return self._handle(request, PendingOwnerSend.Status.DISMISSED, "owner_send.dismissed")
