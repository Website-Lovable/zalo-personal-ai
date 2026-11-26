from django.urls import path

from .webhook import PersonalSidecarWebhookView

urlpatterns = [
    path(
        "webhooks/zalo-personal/<uuid:key>/",
        PersonalSidecarWebhookView.as_view(),
        name="webhook-zalo-personal",
    ),
]
