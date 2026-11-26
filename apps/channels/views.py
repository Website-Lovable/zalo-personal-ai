from django.db import transaction
from django.db.models import Exists, OuterRef
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.audit import client_ip, log_action
from common.tenancy import TenantScopedViewSet

from .models import RISK_STATEMENT_TEXT, RISK_STATEMENT_VERSION, Channel, ChannelRiskAcknowledgement
from .providers.http import ZaloAPIError
from .serializers import (
    BotConnectSerializer,
    ChannelSerializer,
    OAuthStartSerializer,
    RiskAcknowledgementSerializer,
    RiskStatementSerializer,
    TaskQueuedSerializer,
    WebhookInfoSerializer,
)
from .services import (
    ChannelConnectError,
    connect_bot,
    disconnect_channel,
    refresh_oa_token,
    require_real_backend,
    start_oa_oauth,
    webhook_url,
)


class ChannelViewSet(TenantScopedViewSet):
    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer
    filterset_fields = ["channel_type", "mode", "is_active", "status", "data_origin"]
    audit_prefix = "channel"
    action_roles = {
        "acknowledge_risk": "owner",
        "oauth_start": "owner",
        "bot_connect": "owner",
        "disconnect": "owner",
        "sync_contacts": "admin",
        "refresh_token": "admin",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            # One query for the whole page instead of one per channel (Phase 5 N+1 guard).
            current = ChannelRiskAcknowledgement.objects.filter(
                channel=OuterRef("pk"),
                revoked_at__isnull=True,
                statement_version=RISK_STATEMENT_VERSION,
            )
            qs = qs.annotate(risk_ack_cached=Exists(current))
        return qs

    def perform_update(self, serializer):
        secrets_changed = {"access_token", "refresh_token", "webhook_secret"} & set(
            serializer.validated_data
        )
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

    # --- Phase 2: connecting Zalo OA / Zalo Bot ------------------------------------------

    def _require_type(self, channel, channel_type):
        if channel.channel_type != channel_type:
            raise ValidationError({"channel_type": f"Chỉ áp dụng cho kênh {channel_type}."})

    @extend_schema(request=None, responses=OAuthStartSerializer)
    @action(detail=True, methods=["post"], url_path="oauth/start")
    def oauth_start(self, request, pk=None):
        """Start Zalo OA OAuth v4 (PKCE). Open ``authorize_url`` as the OA admin."""
        channel = self.get_object()
        self._require_type(channel, "zalo_oa")
        try:
            url = start_oa_oauth(channel, request.user)
        except ChannelConnectError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response({"authorize_url": url})

    @extend_schema(request=BotConnectSerializer, responses=ChannelSerializer)
    @action(detail=True, methods=["post"], url_path="bot/connect")
    def bot_connect(self, request, pk=None):
        """Connect a Zalo Bot with its token (checked with getMe), via webhook or polling."""
        channel = self.get_object()
        self._require_type(channel, "zalo_bot")
        serializer = BotConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            connect_bot(
                channel,
                bot_token=serializer.validated_data["bot_token"],
                delivery=serializer.validated_data["delivery"],
                user=request.user,
            )
        except ChannelConnectError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(ChannelSerializer(channel, context=self.get_serializer_context()).data)

    @extend_schema(request=None, responses=ChannelSerializer)
    @action(detail=True, methods=["post"])
    def disconnect(self, request, pk=None):
        channel = disconnect_channel(self.get_object(), request.user)
        return Response(ChannelSerializer(channel, context=self.get_serializer_context()).data)

    @extend_schema(request=None, responses=TaskQueuedSerializer)
    @action(detail=True, methods=["post"], url_path="sync-contacts")
    def sync_contacts(self, request, pk=None):
        """Queue a copy of the OA's followers into Contacts (no consent is granted)."""
        from .tasks import sync_channel_contacts

        channel = self.get_object()
        self._require_type(channel, "zalo_oa")
        try:
            require_real_backend()
        except ChannelConnectError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        log_action("channel.contacts_sync_requested", obj=channel, request=request)
        transaction.on_commit(lambda: sync_channel_contacts.delay(channel.pk))
        return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)

    @extend_schema(request=None, responses=ChannelSerializer)
    @action(detail=True, methods=["post"], url_path="refresh-token")
    def refresh_token(self, request, pk=None):
        channel = self.get_object()
        self._require_type(channel, "zalo_oa")
        try:
            require_real_backend()
            refresh_oa_token(channel, force=True)
        except ChannelConnectError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except ZaloAPIError as exc:
            raise ValidationError(
                {"detail": "Zalo từ chối làm mới token; hãy kết nối lại OA."}
            ) from exc
        return Response(ChannelSerializer(channel, context=self.get_serializer_context()).data)

    @extend_schema(responses=WebhookInfoSerializer)
    @action(detail=True, methods=["get"], url_path="webhook-info")
    def webhook_info(self, request, pk=None):
        """URLs to paste into the Zalo developer console for this channel."""
        from django.conf import settings

        channel = self.get_object()
        try:
            url = webhook_url(channel) if channel.channel_type != "zalo_personal" else ""
        except ChannelConnectError:
            url = ""
        return Response(
            {
                "webhook_url": url,
                "oauth_redirect_uri": settings.ZALO_OA_OAUTH_REDIRECT_URI
                if channel.channel_type == "zalo_oa"
                else "",
                "delivery": channel.config.get("delivery", "webhook"),
                "backend": settings.ZALO_CHANNEL_BACKEND,
            }
        )
