from django.shortcuts import render
from django.views.decorators.cache import never_cache
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.rbac import IsTenantMember
from common.tenancy import get_request_tenant

from . import analytics
from .notifications import TICKET_MAX_AGE_SECONDS, make_ticket

STAT_FIELDS = [
    "channels",
    "channels_active",
    "channels_restricted",
    "contacts",
    "contacts_with_consent",
    "conversations",
    "conversations_taken_over",
    "pending_approvals",
    "pending_escalations",
    "my_pending_approvals",
    "pending_owner_sends",
    "messages_in_today",
    "messages_out_today",
    "ai_auto_sent_today",
]


class StatsView(APIView):
    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["dashboard"],
        responses=inline_serializer(
            "DashboardStats",
            {
                **{name: serializers.IntegerField() for name in STAT_FIELDS},
                "emergency_stop": serializers.BooleanField(),
            },
        ),
    )
    def get(self, request):
        return Response(analytics.overview(get_request_tenant(request), request.user))


class AnalyticsView(APIView):
    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["dashboard"],
        parameters=[OpenApiParameter("days", int, description="1–90, mặc định 7")],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request):
        try:
            days = int(request.query_params.get("days", 7))
        except ValueError:
            days = 7
        return Response(analytics.analytics(get_request_tenant(request), days))


class ActivityView(APIView):
    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["dashboard"],
        parameters=[OpenApiParameter("limit", int, description="1–100, mặc định 30")],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 30))
        except ValueError:
            limit = 30
        return Response({"results": analytics.activity(get_request_tenant(request), limit)})


class NotificationTicketView(APIView):
    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["dashboard"],
        request=None,
        responses=inline_serializer(
            "NotificationTicket",
            {
                "ticket": serializers.CharField(),
                "ws_path": serializers.CharField(),
                "expires_in": serializers.IntegerField(),
            },
        ),
    )
    def post(self, request):
        return Response(
            {
                "ticket": make_ticket(request.user),
                "ws_path": "/ws/notifications/",
                "expires_in": TICKET_MAX_AGE_SECONDS,
            }
        )


# The UI is a static single page that talks to the JWT API; no data is rendered server-side.
UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; "
    "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@never_cache
def app_shell(request, path=""):
    response = render(request, "dashboard/app.html")
    response["Content-Security-Policy"] = UI_CSP
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "same-origin"
    return response
