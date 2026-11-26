from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.rbac import IsTenantMember, RolePermission
from apps.ai_engine import control
from common.audit import log_action
from common.tenancy import TenantScopedViewSet, get_request_tenant

from .models import Conversation, Message, PendingOwnerSend
from .serializers import (
    ConversationSerializer,
    MessageSerializer,
    PendingOwnerSendSerializer,
    ReplySerializer,
    TakeoverSerializer,
)
from .services import ReplyNotAllowed, SendFailed, dispatch_reply


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

    @extend_schema(request=ReplySerializer, responses=MessageSerializer)
    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        """Staff reply written by a person (not AI), sent through the channel provider."""
        conversation = self.get_object()
        serializer = ReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = dispatch_reply(
                conversation, serializer.validated_data["text"], is_ai_generated=False
            )
        except ReplyNotAllowed as exc:
            raise ValidationError({"detail": "Liên hệ này không được phép nhận tin."}) from exc
        except SendFailed as exc:
            log_action(
                "message.send_failed",
                obj=conversation,
                changes={"error_code": exc.code, "error": str(exc)[:200]},
                request=request,
            )
            return Response({"detail": f"Zalo không nhận tin: {exc}"}, status=502)
        if result.pending is not None:
            log_action("message.reply_queued", obj=result.pending, request=request)
            return Response({"detail": "Đã đưa vào hộp 'Chờ chủ gửi'."}, status=202)
        log_action("message.sent_by_staff", obj=result.message, request=request)
        # A person is talking now: keep the AI quiet for a while (Blueprint C6 #8).
        control.auto_take_over(conversation, user=request.user)
        return Response(MessageSerializer(result.message).data, status=201)

    @extend_schema(request=TakeoverSerializer, responses=ConversationSerializer)
    @action(detail=True, methods=["post"])
    def takeover(self, request, pk=None):
        """Human takeover: the AI stops answering this conversation (optionally for N minutes)."""
        conversation = self.get_object()
        serializer = TakeoverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        control.take_over(
            conversation,
            user=request.user,
            minutes=serializer.validated_data.get("minutes"),
            request=request,
        )
        return Response(self.get_serializer(conversation).data)

    @extend_schema(request=None, responses=ConversationSerializer)
    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        """Give the conversation back to the AI."""
        conversation = self.get_object()
        control.release(conversation, user=request.user, request=request)
        return Response(self.get_serializer(conversation).data)


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
