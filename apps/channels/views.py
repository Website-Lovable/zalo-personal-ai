from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.audit import client_ip, log_action
from common.tenancy import TenantScopedViewSet

from .models import RISK_STATEMENT_TEXT, RISK_STATEMENT_VERSION, Channel, ChannelRiskAcknowledgement
from .serializers import (
    ChannelSerializer,
    RiskAcknowledgementSerializer,
    RiskStatementSerializer,
)


class ChannelViewSet(TenantScopedViewSet):
    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer
    filterset_fields = ["channel_type", "mode", "is_active", "status", "data_origin"]
    audit_prefix = "channel"
    action_roles = {"acknowledge_risk": "owner"}

    def perform_update(self, serializer):
        secrets_changed = {"access_token", "refresh_token"} & set(serializer.validated_data)
        super().perform_update(serializer)
        if secrets_changed:
            log_action(
                "channel.credentials_changed",
                obj=serializer.instance,
                changes={"fields": sorted(secrets_changed)},
                request=self.request,
            )

    @extend_schema(responses=RiskStatementSerializer)
    @action(detail=True, methods=["get"], url_path="risk-statement")
    def risk_statement(self, request, pk=None):
        self.get_object()
        return Response(
            {"statement_version": RISK_STATEMENT_VERSION, "statement_text": RISK_STATEMENT_TEXT}
        )

    @extend_schema(request=RiskAcknowledgementSerializer, responses=ChannelSerializer)
    @action(detail=True, methods=["post"], url_path="acknowledge-risk")
    def acknowledge_risk(self, request, pk=None):
        """Owner's written risk acceptance for a zalo_personal channel (recorded in AuditLog)."""
        channel = self.get_object()
        if not channel.is_personal:
            raise ValidationError({"channel_type": "Chỉ áp dụng cho kênh Zalo cá nhân."})
        serializer = RiskAcknowledgementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            ack = ChannelRiskAcknowledgement.objects.create(
                tenant=channel.tenant,
                channel=channel,
                acknowledged_by=request.user,
                full_name=serializer.validated_data["full_name"],
                statement_version=RISK_STATEMENT_VERSION,
                statement_text=RISK_STATEMENT_TEXT,
                ip_address=client_ip(request),
                data_origin=channel.data_origin,
            )
            log_action(
                "channel.risk_acknowledged",
                obj=channel,
                changes={
                    "acknowledgement_id": ack.pk,
                    "statement_version": RISK_STATEMENT_VERSION,
                    "full_name": ack.full_name,
                },
                request=request,
            )
        return Response(
            ChannelSerializer(channel, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )
