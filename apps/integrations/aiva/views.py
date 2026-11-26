"""HTTP layer of the AIVA integration.

1. ``/api/integration/v1/...`` — called by AIVA (service token + HMAC, ``inbound_auth``).
2. ``/api/integrations/aiva/...`` — tenant users (JWT) watch the integration: status, outbox,
   exchange log, and re-queue a dead event (admin).
3. ``/api/integrations/aiva/live/`` (Phase 6) — the tenant's live enrollment: review, owner
   acknowledgement, pause (admin) and revoke (owner). Activation and resume are operator
   commands (``manage.py aiva_live``).
"""

from __future__ import annotations

import hashlib
import json

from django.db import IntegrityError, transaction
from django.db.models import Count
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.rbac import IsTenantMember, RolePermission
from common.audit import log_action
from common.tenancy import get_request_tenant

from . import config, delivery, inbound, live
from .inbound_auth import AIVAServiceAuthentication, audit_inbound
from .models import IntegrationAuditLog, IntegrationInboundRequest, IntegrationOutbox
from .preflight import run_preflight

MAX_BODY_BYTES = 64 * 1024
IDEMPOTENCY_HEADER = "Idempotency-Key"


class AIVAServiceView(APIView):
    authentication_classes = [AIVAServiceAuthentication]
    permission_classes: list = []
    endpoint = ""

    def tenant(self, request):
        return request.user.tenant

    def respond(self, request, status: int, body: dict, *, data_origin: str = "") -> Response:
        audit_inbound(
            request,
            tenant=self.tenant(request),
            success=200 <= status < 300,
            status=status,
            event_type=self.endpoint,
            data_origin=data_origin,
            detail=body.get("code", "ok") if isinstance(body, dict) else "",
        )
        return Response(body, status=status)


class AIVAWriteView(AIVAServiceView):
    """POST with a JSON body and a mandatory ``Idempotency-Key``."""

    handler = None  # inbound.<function>(tenant, data) -> (status, body)

    @extend_schema(exclude=True)
    def post(self, request):
        tenant = self.tenant(request)
        raw = request.body
        if len(raw) > MAX_BODY_BYTES:
            return self.respond(request, *inbound.error(413, "too_large", "Body quá lớn."))
        key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
        if not key or len(key) > 100:
            return self.respond(
                request, *inbound.error(400, "idempotency_key_required", IDEMPOTENCY_HEADER)
            )
        digest = hashlib.sha256(raw).hexdigest()
        previous = IntegrationInboundRequest.objects.filter(
            tenant=tenant, idempotency_key=key
        ).first()
        if previous is not None:
            return self._replay(request, previous, digest)
        try:
            data = json.loads(raw or b"null")
        except ValueError:
            return self.respond(request, *inbound.error(400, "invalid_json", "Body không hợp lệ."))
        origin = data.get("data_origin", "") if isinstance(data, dict) else ""
        try:
            with transaction.atomic():
                status, body = type(self).handler(tenant, data)
                IntegrationInboundRequest.objects.create(
                    tenant=tenant,
                    idempotency_key=key,
                    endpoint=self.endpoint,
                    body_sha256=digest,
                    response_status=status,
                    response_body=body,
                )
        except IntegrityError:  # same key raced in: answer like the first one
            previous = IntegrationInboundRequest.objects.get(tenant=tenant, idempotency_key=key)
            return self._replay(request, previous, digest)
        return self.respond(request, status, body, data_origin=str(origin)[:10])

    def _replay(self, request, previous, digest) -> Response:
        if previous.body_sha256 != digest or previous.endpoint != self.endpoint:
            return self.respond(
                request,
                *inbound.error(409, "idempotency_key_reused", "Key đã dùng cho nội dung khác."),
            )
        response = self.respond(request, previous.response_status, previous.response_body)
        response["Idempotent-Replayed"] = "true"
        return response


class ContactUpsertView(AIVAWriteView):
    endpoint = "contacts.upsert"
    handler = staticmethod(inbound.upsert_contact)

    @extend_schema(
        tags=["integration-v1"],
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT},
        description="CRS đẩy khách hàng (có consent). Body: aiva-v1-inbound.schema.json "
        "#/$defs/contacts_upsert.",
    )
    def post(self, request):
        return super().post(request)


class OutreachDraftView(AIVAWriteView):
    endpoint = "outreach.draft"
    handler = staticmethod(inbound.outreach_draft)

    @extend_schema(
        tags=["integration-v1"],
        request=OpenApiTypes.OBJECT,
        responses={201: OpenApiTypes.OBJECT},
        description="Sales AI gửi bản nháp tin chăm sóc → hàng đợi duyệt của nhân viên phụ "
        "trách. Không tự gửi. Body: #/$defs/outreach_draft.",
    )
    def post(self, request):
        return super().post(request)


class ConversationSummaryView(AIVAServiceView):
    endpoint = "conversations.summary"

    @extend_schema(tags=["integration-v1"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk: int):
        status, body = inbound.conversation_summary(self.tenant(request), pk)
        return self.respond(request, status, body, data_origin=body.get("data_origin", ""))


class HealthView(AIVAServiceView):
    endpoint = "health"

    @extend_schema(tags=["integration-v1"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return self.respond(request, *inbound.health(self.tenant(request)))


# --- Tenant-facing monitoring (JWT) --------------------------------------------------


class OutboxSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegrationOutbox
        fields = [
            "id",
            "event_id",
            "event_type",
            "status",
            "attempts",
            "next_attempt_at",
            "last_error",
            "sent_at",
            "data_origin",
            "created_at",
            "payload",
        ]
        read_only_fields = fields


class ExchangeLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegrationAuditLog
        fields = [
            "id",
            "direction",
            "mode",
            "event_type",
            "event_id",
            "data_origin",
            "success",
            "status_code",
            "detail",
            "created_at",
        ]
        read_only_fields = fields


class IntegrationStatusView(APIView):
    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["integrations"],
        responses=inline_serializer(
            "AIVAIntegrationStatus",
            {
                "mode": serializers.CharField(),
                "enabled": serializers.BooleanField(),
                "live_approved": serializers.BooleanField(),
                "client": serializers.CharField(),
                "tenant_ref": serializers.CharField(),
                "allowed_data_origins": serializers.ListField(child=serializers.CharField()),
                "outbox": serializers.DictField(child=serializers.IntegerField()),
            },
        ),
    )
    def get(self, request):
        tenant = get_request_tenant(request)
        counts = dict(
            IntegrationOutbox.objects.for_tenant(tenant)
            .values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )
        return Response(
            {
                "mode": config.get_mode(),
                "enabled": config.is_enabled(),
                "live_approved": config.live_approved(),
                "client": config.client_backend(),
                "tenant_ref": tenant.external_ref,
                "allowed_data_origins": sorted(config.allowed_data_origins()),
                "outbox": {s: counts.get(s, 0) for s in IntegrationOutbox.Status.values},
                "live_exchange": live.can_exchange(tenant)[0] if config.is_enabled() else False,
            }
        )


def _live_payload(tenant) -> dict:
    enrollment = live.enrollment_for(tenant)
    switch = live.get_switch()
    report = run_preflight(tenant=tenant)
    return {
        "mode": config.get_mode(),
        "live_approved": config.live_approved(),
        "global_paused": switch.paused,
        "live_exchange": config.get_mode() == config.LIVE and live.can_exchange(tenant)[0],
        "acknowledgement_text": live.ACKNOWLEDGEMENT_TEXT,
        "acknowledgement_version": live.ACKNOWLEDGEMENT_VERSION,
        "enrollment": None
        if enrollment is None
        else {
            "status": enrollment.status,
            "acknowledged_by": enrollment.acknowledged_by.get_username()
            if enrollment.acknowledged_by
            else "",
            "acknowledged_at": enrollment.acknowledged_at,
            "activated_at": enrollment.activated_at,
            "approval_ref": enrollment.approval_ref,
            "paused_at": enrollment.paused_at,
            "pause_reason": enrollment.pause_reason,
        },
        "review": report.as_dict(),
    }


class LiveStatusView(APIView):
    """Live enrollment of the caller's tenant plus the Phase 6 review (admin and owner)."""

    permission_classes = [IsTenantMember, RolePermission]
    read_role = "admin"

    @extend_schema(tags=["integrations"], responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(_live_payload(get_request_tenant(request)))


class LiveActionView(APIView):
    """POST acknowledge (owner) | pause (admin, needs reason) | revoke (owner)."""

    permission_classes = [IsTenantMember, RolePermission]
    write_role = "admin"
    OWNER_ONLY = {"acknowledge", "revoke"}

    @extend_schema(
        tags=["integrations"],
        request=inline_serializer(
            "AIVALiveAction",
            {
                "reason": serializers.CharField(required=False),
                "confirm": serializers.BooleanField(required=False),
            },
        ),
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request, op: str):
        tenant = get_request_tenant(request)
        user = request.user
        if op not in {"acknowledge", "pause", "revoke"}:
            return Response({"detail": "Thao tác không hợp lệ."}, status=404)
        if op in self.OWNER_ONLY and not user.has_role("owner"):
            return Response({"detail": "Chỉ chủ tài khoản được thực hiện."}, status=403)
        reason = str(request.data.get("reason", "")).strip()[:500]
        by = f"{user.get_username()} (app)"
        try:
            if op == "acknowledge":
                if request.data.get("confirm") is not True:
                    return Response(
                        {"detail": "Cần đánh dấu xác nhận đã đọc nội dung (confirm=true)."},
                        status=400,
                    )
                live.acknowledge(tenant, user, request=request)
            elif op == "pause":
                live.pause(tenant, by=by, reason=reason, user=user, request=request)
            else:
                live.revoke(tenant, by=by, reason=reason, user=user, request=request)
        except live.LiveStateError as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(_live_payload(tenant))


class OutboxViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = OutboxSerializer
    permission_classes = [IsTenantMember, RolePermission]
    action_roles = {"retry": "admin"}
    filterset_fields = ["status", "event_type", "data_origin"]
    queryset = IntegrationOutbox.objects.all()

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(tenant=get_request_tenant(self.request))
            .order_by("-created_at")
        )

    @extend_schema(request=None, responses=OutboxSerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        item = delivery.requeue(self.get_object())
        log_action("aiva.outbox_requeued", obj=item, request=request)
        return Response(self.get_serializer(item).data)


class ExchangeLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ExchangeLogSerializer
    permission_classes = [IsTenantMember]
    filterset_fields = ["direction", "success", "event_type"]
    queryset = IntegrationAuditLog.objects.all()

    def get_queryset(self):
        return super().get_queryset().filter(tenant=get_request_tenant(self.request))
