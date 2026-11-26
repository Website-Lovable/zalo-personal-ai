"""REST API for personal Zalo channels: QR login, emergency stop, resume, guard status.

Mounted at ``/api/personal-channels/`` (see ``urls.py``). Only ``zalo_personal`` channels of
the caller's tenant are visible. The risk statement and acknowledgement endpoints stay on
``/api/channels/{id}/`` (Phase 1).
"""

from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.accounts.rbac import IsTenantMember, RolePermission
from apps.channels.models import Channel, ChannelType
from common.exceptions import FeatureDisabledError
from common.tenancy import get_request_tenant

from . import control
from .adapter import SidecarError
from .limits import Limits, stop_state, usage_counts


class LoginStartSerializer(serializers.Serializer):
    login_id = serializers.CharField(read_only=True)
    qr_image = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)


class LoginStatusRequestSerializer(serializers.Serializer):
    login_id = serializers.CharField(max_length=128)


class LoginStatusSerializer(serializers.Serializer):
    state = serializers.CharField(read_only=True)
    account_id = serializers.CharField(read_only=True, required=False)
    display_name = serializers.CharField(read_only=True, required=False)


class StopSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class ResumeSerializer(serializers.Serializer):
    restriction_reviewed = serializers.BooleanField(required=False, default=False)


class PersonalChannelSerializer(serializers.ModelSerializer):
    zalo_account_id = serializers.SerializerMethodField()
    zalo_display_name = serializers.SerializerMethodField()
    emergency_stop = serializers.SerializerMethodField()
    auto_stopped = serializers.SerializerMethodField()
    logged_in = serializers.SerializerMethodField()
    risk_acknowledged = serializers.BooleanField(read_only=True)

    class Meta:
        model = Channel
        fields = [
            "id",
            "name",
            "mode",
            "status",
            "is_active",
            "risk_acknowledged",
            "logged_in",
            "zalo_account_id",
            "zalo_display_name",
            "emergency_stop",
            "auto_stopped",
            "last_error",
            "connected_at",
            "data_origin",
        ]
        read_only_fields = fields

    def _config(self, obj):
        return obj.config or {}

    def get_zalo_account_id(self, obj) -> str:
        return self._config(obj).get("zalo_account_id", "")

    def get_zalo_display_name(self, obj) -> str:
        return self._config(obj).get("zalo_display_name", "")

    def get_emergency_stop(self, obj) -> dict | None:
        return self._config(obj).get("emergency_stop")

    def get_auto_stopped(self, obj) -> dict | None:
        return self._config(obj).get("auto_stopped")

    def get_logged_in(self, obj) -> bool:
        return bool(obj.session_data)


class PersonalChannelViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = PersonalChannelSerializer
    permission_classes = [IsTenantMember, RolePermission]
    queryset = Channel.objects.filter(channel_type=ChannelType.ZALO_PERSONAL)
    action_roles = {
        "login_start": "owner",
        "login_status": "owner",
        "logout": "owner",
        "resume": "owner",
        "emergency_stop": "admin",
    }

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # Emergency stop and the read-only views keep working with the flag off.
        if not settings.ZALO_PERSONAL_UNOFFICIAL_ENABLED and self.action in {
            "login_start",
            "login_status",
            "resume",
        }:
            raise NotFound("Tính năng Zalo cá nhân đang tắt.")

    def get_queryset(self):
        return super().get_queryset().filter(tenant=get_request_tenant(self.request))

    def _data(self, channel):
        return PersonalChannelSerializer(channel).data

    def _run(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except control.PersonalChannelError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except FeatureDisabledError as exc:
            raise NotFound("Tính năng Zalo cá nhân đang tắt.") from exc
        except SidecarError as exc:
            raise ValidationError(
                {"detail": "Không kết nối được dịch vụ đăng nhập Zalo cá nhân (sidecar)."}
            ) from exc

    @extend_schema(request=None, responses=LoginStartSerializer)
    @action(detail=True, methods=["post"], url_path="login/start")
    def login_start(self, request, pk=None):
        """Create a QR code. The owner scans it with Zalo on their own phone."""
        channel = self.get_object()
        data = self._run(control.start_login, channel, request.user)
        return Response(LoginStartSerializer(data).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=LoginStatusRequestSerializer, responses=LoginStatusSerializer)
    @action(detail=True, methods=["post"], url_path="login/status")
    def login_status(self, request, pk=None):
        channel = self.get_object()
        serializer = LoginStatusRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = self._run(
            control.poll_login, channel, request.user, serializer.validated_data["login_id"]
        )
        return Response(data)

    @extend_schema(request=None, responses=PersonalChannelSerializer)
    @action(detail=True, methods=["post"])
    def logout(self, request, pk=None):
        channel = self._run(control.logout, self.get_object(), request.user)
        return Response(self._data(channel))

    @extend_schema(request=StopSerializer, responses=PersonalChannelSerializer)
    @action(detail=True, methods=["post"], url_path="emergency-stop")
    def emergency_stop(self, request, pk=None):
        """Stop every send on this channel now. Admins and owners."""
        serializer = StopSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel = self._run(
            control.emergency_stop,
            self.get_object(),
            request.user,
            serializer.validated_data["reason"],
        )
        return Response(self._data(channel))

    @extend_schema(request=ResumeSerializer, responses=PersonalChannelSerializer)
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        serializer = ResumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel = self._run(
            control.resume,
            self.get_object(),
            request.user,
            restriction_reviewed=serializer.validated_data["restriction_reviewed"],
        )
        return Response(self._data(channel))

    @action(detail=True, methods=["get"])
    def limits(self, request, pk=None):
        """Today's usage against the caps, and whether sending is currently allowed."""
        channel = self.get_object()
        limits = Limits.for_channel(channel)
        stopped = stop_state(channel)
        return Response(
            {
                "can_send": stopped is None,
                "blocked_reason": stopped.reason if stopped else "",
                "blocked_message": stopped.message if stopped else "",
                "usage": usage_counts(channel),
                "limits": {
                    "send_window": f"{limits.window_start:%H:%M}-{limits.window_end:%H:%M}",
                    "per_minute": limits.per_minute,
                    "per_hour": limits.per_hour,
                    "per_day": limits.per_day,
                    "per_contact_per_hour": limits.per_contact_per_hour,
                    "min_gap_seconds": limits.min_gap_seconds,
                    "min_reply_delay_seconds": limits.min_reply_delay_seconds,
                    "reply_window_hours": limits.reply_window_hours,
                },
            }
        )
