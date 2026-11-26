from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from apps.accounts.urls import auth_urlpatterns
from apps.ai_engine.views import CommunicationStyleView, OwnerProfileView
from apps.channels.views import ChannelViewSet
from apps.contacts.views import ContactGroupViewSet, ContactViewSet
from apps.conversations.views import ConversationViewSet, PendingOwnerSendViewSet
from apps.dashboard.views import StatsView
from apps.permissions.views import ApprovalViewSet, PermissionRuleViewSet

router = DefaultRouter()
router.register("channels", ChannelViewSet, basename="channel")
router.register("contacts", ContactViewSet, basename="contact")
router.register("contact-groups", ContactGroupViewSet, basename="contact-group")
router.register("conversations", ConversationViewSet, basename="conversation")
router.register("owner-sends", PendingOwnerSendViewSet, basename="owner-send")
router.register("permissions/rules", PermissionRuleViewSet, basename="permission-rule")
router.register("approvals", ApprovalViewSet, basename="approval")


def healthz(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("api/auth/", include(auth_urlpatterns)),
    path("api/ai/profile/", OwnerProfileView.as_view(), name="ai-profile"),
    path("api/ai/style/", CommunicationStyleView.as_view(), name="ai-style"),
    path("api/dashboard/stats/", StatsView.as_view(), name="dashboard-stats"),
    path("api/", include("apps.accounts.urls")),
    path("api/", include(router.urls)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
