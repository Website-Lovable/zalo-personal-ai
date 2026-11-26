from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.rbac import IsTenantMember, RolePermission
from common.audit import log_action
from common.tenancy import get_request_tenant

from . import control, pipeline
from .llm import LLMError
from .models import AIControl, AIReplyLog, CommunicationStyle, OwnerProfile
from .serializers import (
    AIControlSerializer,
    AIReplyLogSerializer,
    CommunicationStyleSerializer,
    EmergencyStopSerializer,
    OwnerProfileSerializer,
    PreviewRequestSerializer,
    PreviewResponseSerializer,
)


class _TenantSingletonView(APIView):
    """GET/PUT/PATCH a one-per-tenant settings object (created on first access)."""

    permission_classes = [IsTenantMember, RolePermission]
    model = None
    serializer_class = None
    audit_action = ""

    def get_object(self):
        obj, _ = self.model.objects.get_or_create(tenant=get_request_tenant(self.request))
        return obj

    def get(self, request):
        return Response(self.serializer_class(self.get_object()).data)

    def _write(self, request, partial):
        obj = self.get_object()
        serializer = self.serializer_class(obj, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_action(
            self.audit_action, obj=obj, changes=dict(serializer.validated_data), request=request
        )
        return Response(serializer.data)

    def put(self, request):
        return self._write(request, partial=False)

    def patch(self, request):
        return self._write(request, partial=True)


@extend_schema(request=OwnerProfileSerializer, responses=OwnerProfileSerializer)
class OwnerProfileView(_TenantSingletonView):
    model = OwnerProfile
    serializer_class = OwnerProfileSerializer
    audit_action = "ai_profile.updated"


@extend_schema(request=CommunicationStyleSerializer, responses=CommunicationStyleSerializer)
class CommunicationStyleView(_TenantSingletonView):
    model = CommunicationStyle
    serializer_class = CommunicationStyleSerializer
    audit_action = "ai_style.updated"


class AIControlView(APIView):
    """GET status of the AI switches; PATCH reply policy (owner)."""

    permission_classes = [IsTenantMember, RolePermission]
    write_role = "owner"

    @extend_schema(responses=AIControlSerializer)
    def get(self, request):
        return Response(AIControlSerializer(AIControl.for_tenant(get_request_tenant(request))).data)

    @extend_schema(request=AIControlSerializer, responses=AIControlSerializer)
    def patch(self, request):
        obj = AIControl.for_tenant(get_request_tenant(request))
        serializer = AIControlSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_action(
            "ai_control.updated", obj=obj, changes=dict(serializer.validated_data), request=request
        )
        return Response(serializer.data)


class EmergencyStopView(APIView):
    """Emergency stop: every AI reply and draft for this tenant stops at once (admin+)."""

    permission_classes = [IsTenantMember, RolePermission]
    write_role = "admin"

    @extend_schema(request=EmergencyStopSerializer, responses=AIControlSerializer)
    def post(self, request):
        serializer = EmergencyStopSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj = control.emergency_stop(
            get_request_tenant(request),
            user=request.user,
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        return Response(AIControlSerializer(obj).data)


class ResumeView(APIView):
    """Turn the AI back on after an emergency stop (owner only)."""

    permission_classes = [IsTenantMember, RolePermission]
    write_role = "owner"

    @extend_schema(request=None, responses=AIControlSerializer)
    def post(self, request):
        obj = control.resume(get_request_tenant(request), user=request.user, request=request)
        return Response(AIControlSerializer(obj).data)


class PreviewView(APIView):
    """Dry run: what would the AI reply to the latest message of a conversation? Sends nothing."""

    permission_classes = [IsTenantMember, RolePermission]
    write_role = "admin"

    @extend_schema(request=PreviewRequestSerializer, responses=PreviewResponseSerializer)
    def post(self, request):
        from apps.conversations.models import Conversation

        serializer = PreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = get_object_or_404(
            Conversation.objects.select_related("contact", "channel", "tenant"),
            pk=serializer.validated_data["conversation"],
            tenant=get_request_tenant(request),
        )
        try:
            decision, draft = pipeline.preview(conversation)
        except LLMError as exc:
            return Response({"detail": f"Không gọi được mô hình AI: {exc}"}, status=502)
        return Response(
            {
                "action": decision.action,
                "skip": decision.skip,
                "reasons": decision.reasons,
                "draft": draft.draft,
                "confidence": draft.confidence,
                "model": draft.model,
            }
        )


class AIReplyLogViewSet(viewsets.ReadOnlyModelViewSet):
    """What the AI decided for each inbound message, and why."""

    serializer_class = AIReplyLogSerializer
    permission_classes = [IsTenantMember, RolePermission]
    filterset_fields = ["outcome", "conversation", "decision"]
    queryset = AIReplyLog.objects.all()

    def get_queryset(self):
        return super().get_queryset().filter(tenant=get_request_tenant(self.request))
