"""AIVA -> Zalo AI endpoints, mounted at /api/integration/v1/ (Kickoff 2.2)."""

from django.urls import path

from .views import ContactUpsertView, ConversationSummaryView, HealthView, OutreachDraftView

app_name = "aiva_v1"

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path("contacts/upsert", ContactUpsertView.as_view(), name="contacts-upsert"),
    path("outreach/draft", OutreachDraftView.as_view(), name="outreach-draft"),
    path(
        "conversations/<int:pk>/summary",
        ConversationSummaryView.as_view(),
        name="conversation-summary",
    ),
]
