"""Events pushed by the sidecar: ``POST /webhooks/zalo-personal/<channel webhook_key>/``.

Event types (all signed with the shared sidecar secret, see ``provider.verify_signature``):
- ``message``: a 1-1 message (inbound, or the owner's own when ``is_self``) -> inbox.
- ``restriction``: a restriction sign (``kind``, ``detail``) -> the channel stops itself.
- ``session_updated``: refreshed session cookies -> stored encrypted.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.http import Http404, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.channels.models import Channel, ChannelType

from . import control
from .provider import verify_signature

MAX_BODY_BYTES = 256 * 1024


@method_decorator(csrf_exempt, name="dispatch")
class PersonalSidecarWebhookView(View):
    def post(self, request, key):
        if not settings.ZALO_PERSONAL_UNOFFICIAL_ENABLED:
            raise Http404
        if len(request.body) > MAX_BODY_BYTES:
            return JsonResponse({"status": "rejected", "reason": "too large"}, status=400)
        if not verify_signature(request.headers, request.body):
            return JsonResponse({"status": "rejected", "reason": "bad signature"}, status=401)
        channel = Channel.objects.filter(
            webhook_key=key, channel_type=ChannelType.ZALO_PERSONAL, is_active=True
        ).first()
        if channel is None:
            raise Http404
        try:
            payload = json.loads(request.body)
        except ValueError:
            return JsonResponse({"status": "rejected", "reason": "invalid body"}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({"status": "rejected", "reason": "invalid body"}, status=400)

        kind = payload.get("type")
        known = (channel.config or {}).get("zalo_account_id")
        account_id = str(payload.get("account_id") or "")
        if known and account_id and account_id != known and kind != "restriction":
            control.auto_stop(channel, "account_changed", "event from another Zalo account")
            return JsonResponse({"status": "stopped"})
        if kind == "restriction":
            restriction = payload.get("restriction") or {}
            control.auto_stop(
                channel,
                str(restriction.get("kind") or "warning")[:50],
                str(restriction.get("detail") or "")[:500],
            )
            return JsonResponse({"status": "stopped"})
        if kind == "session_updated":
            control.handle_session_update(
                channel, account_id=account_id, session=str(payload.get("session") or "")
            )
            return JsonResponse({"status": "ok"})
        if kind == "message":
            from apps.channels.services import record_webhook_event

            event = record_webhook_event(channel, payload, source="sidecar")
            return JsonResponse({"status": "ok" if event else "duplicate"})
        return JsonResponse({"status": "ignored"})
