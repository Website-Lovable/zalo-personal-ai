from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from apps.accounts.urls import auth_urlpatterns
from apps.ai_engine.views import (
    AIControlView,
    AIReplyLogViewSet,
    CommunicationStyleView,
    EmergencyStopView,
    OwnerProfileView,
    PreviewView,
    ResumeView,
)
from apps.channels.providers.personal_unofficial.api import PersonalChannelViewSet  # personal
from apps.channels.views import ChannelViewSet
from apps.channels.webhooks import (
    ZaloBotWebhookView,
    ZaloOAWebhookView,
    oa_oauth_callback,
    zalo_site_verification,
)
from apps.contacts.views import ContactGroupViewSet, ContactViewSet
from apps.conversations.views import ConversationViewSet, PendingOwnerSendViewSet
from apps.dashboard.views import (
    ActivityView,
    AnalyticsView,
    NotificationTicketView,
    StatsView,
    app_shell,
)
from apps.integrations.aiva.views import ExchangeLogViewSet, IntegrationStatusView, OutboxViewSet
from apps.memory.views import LongTermMemoryViewSet
from apps.permissions.views import ApprovalViewSet, PermissionRuleViewSet

router = DefaultRouter()
router.register("channels", ChannelViewSet, basename="channel")
router.register("contacts", ContactViewSet, basename="contact")
router.register("contact-groups", ContactGroupViewSet, basename="contact-group")
router.register("conversations", ConversationViewSet, basename="conversation")
router.register("owner-sends", PendingOwnerSendViewSet, basename="owner-send")
router.register("permissions/rules", PermissionRuleViewSet, basename="permission-rule")
router.register("approvals", ApprovalViewSet, basename="approval")
router.register("memories", LongTermMemoryViewSet, basename="memory")
router.register("ai/runs", AIReplyLogViewSet, basename="ai-run")
router.register("integrations/aiva/outbox", OutboxViewSet, basename="aiva-outbox")
router.register("integrations/aiva/exchanges", ExchangeLogViewSet, basename="aiva-exchange")
# Personal Zalo (isolated, flag-gated). Remove with apps/channels/providers/personal_unofficial.
router.register("personal-channels", PersonalChannelViewSet, basename="personal-channel")


def healthz(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    # Called by Zalo (public; authenticity is checked per request)
    path("webhooks/zalo-oa/", ZaloOAWebhookView.as_view(), name="webhook-zalo-oa"),
    path(
        "webhooks/zalo-oa/<uuid:key>/",
        ZaloOAWebhookView.as_view(),
        name="webhook-zalo-oa-channel",
    ),
    path("webhooks/zalo-bot/<uuid:key>/", ZaloBotWebhookView.as_view(), name="webhook-zalo-bot"),
    path("", include("apps.channels.providers.personal_unofficial.urls")),  # personal Zalo
    path("oauth/zalo-oa/callback/", oa_oauth_callback, name="oauth-zalo-oa-callback"),
    path("zalo_verifier<str:code>.html", zalo_site_verification, name="zalo-site-verification"),
    path("api/auth/", include(auth_urlpatterns)),
    path("api/ai/profile/", OwnerProfileView.as_view(), name="ai-profile"),
    path("api/ai/style/", CommunicationStyleView.as_view(), name="ai-style"),
    path("api/ai/control/", AIControlView.as_view(), name="ai-control"),
    path("api/ai/emergency-stop/", EmergencyStopView.as_view(), name="ai-emergency-stop"),
    path("api/ai/resume/", ResumeView.as_view(), name="ai-resume"),
    path("api/ai/preview/", PreviewView.as_view(), name="ai-preview"),
    path("api/dashboard/stats/", StatsView.as_view(), name="dashboard-stats"),
    path("api/dashboard/analytics/", AnalyticsView.as_view(), name="dashboard-analytics"),
    path("api/dashboard/activity/", ActivityView.as_view(), name="dashboard-activity"),
    path("api/notifications/ticket/", NotificationTicketView.as_view(), name="notification-ticket"),
    # Mobile-first admin UI (Phase 4): one static page, client-side routes under /app/.
    path("", RedirectView.as_view(url="/app/", permanent=False)),
    path("app/", app_shell, name="app"),
    # AIVA -> Zalo AI (service token + HMAC; 503 while AIVA_INTEGRATION_MODE=off)
    path("api/integration/v1/", include("apps.integrations.aiva.urls")),
    path(
        "api/integrations/aiva/status/",
        IntegrationStatusView.as_view(),
        name="aiva-integration-status",
    ),
    path("api/", include("apps.accounts.urls")),
    path("api/", include(router.urls)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
