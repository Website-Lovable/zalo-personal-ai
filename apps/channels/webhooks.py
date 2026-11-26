"""Public endpoints Zalo calls: webhooks (OA, Bot), the OA OAuth callback, site verification.

Webhooks must ACK within a few seconds (Zalo OA: < 5 s), so they only verify, store the raw
event (dedupe) and return 200; Celery does the rest (``tasks.process_webhook_event``).
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import Channel, ChannelType
from .providers import provider_class_for
from .providers.zalo_oa import ZaloOAProvider, oa_id_from_payload
from .services import ChannelConnectError, complete_oa_oauth, record_webhook_event

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1024 * 1024


def _reject(status: int, reason: str) -> JsonResponse:
    return JsonResponse({"status": "rejected", "reason": reason}, status=status)


def _parse(request) -> dict | None:
    if len(request.body) > MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(request.body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


@method_decorator(csrf_exempt, name="dispatch")
class ZaloOAWebhookView(View):
    """``/webhooks/zalo-oa/`` (one URL per Zalo app, routed by OA id) and
    ``/webhooks/zalo-oa/<key>/`` (bound to one channel)."""

    def get(self, request, key=None):
        return JsonResponse({"status": "ok"})

    def post(self, request, key=None):
        payload = _parse(request)
        if payload is None:
            return _reject(400, "invalid body")
        oa_id = oa_id_from_payload(payload)
        if key is not None:
            channel = Channel.objects.filter(
                webhook_key=key, channel_type=ChannelType.ZALO_OA
            ).first()
            if channel is None:
                raise Http404
            if channel.zalo_oa_id and oa_id and channel.zalo_oa_id != oa_id:
                return _reject(403, "oa mismatch")
        else:
            channel = (
                Channel.objects.filter(
                    channel_type=ChannelType.ZALO_OA, zalo_oa_id=oa_id, is_active=True
                ).first()
                if oa_id
                else None
            )
        verifier = ZaloOAProvider(channel or Channel(channel_type=ChannelType.ZALO_OA))
        if not verifier.verify_webhook(request.headers, request.body):
            logger.warning("Zalo OA webhook with invalid signature (oa_id=%s)", oa_id)
            return _reject(401, "invalid signature")
        if channel is None or not channel.is_active:
            # Authentic, but for an OA we do not serve: ACK so Zalo stops retrying.
            return JsonResponse({"status": "ignored"})
        record_webhook_event(channel, payload)
        return JsonResponse({"status": "ok"})


@method_decorator(csrf_exempt, name="dispatch")
class ZaloBotWebhookView(View):
    """``/webhooks/zalo-bot/<key>/``: header ``X-Bot-Api-Secret-Token`` must match."""

    def post(self, request, key):
        channel = Channel.objects.filter(webhook_key=key, channel_type=ChannelType.ZALO_BOT).first()
        if channel is None:
            raise Http404
        provider = provider_class_for(channel.channel_type)(channel)
        if not provider.verify_webhook(request.headers, request.body):
            logger.warning("Zalo Bot webhook with invalid secret token (channel=%s)", channel.pk)
            return _reject(403, "invalid secret token")
        payload = _parse(request)
        if payload is None:
            return _reject(400, "invalid body")
        if channel.is_active:
            record_webhook_event(channel, payload)
        return JsonResponse({"status": "ok"})


def oa_oauth_callback(request):
    """Zalo redirects here with ``code``, ``oa_id`` and our ``state`` after OA consent."""
    try:
        channel = complete_oa_oauth(
            state=request.GET.get("state", ""),
            code=request.GET.get("code", ""),
            oa_id=request.GET.get("oa_id", ""),
        )
    except ChannelConnectError as exc:
        return _oauth_result(ok=False, message=str(exc))
    return _oauth_result(ok=True, message=f"Đã kết nối Zalo OA: {channel.name}", channel=channel)


def _oauth_result(*, ok: bool, message: str, channel: Channel | None = None):
    target = settings.ZALO_OA_OAUTH_RESULT_URL
    if target:
        query = {"status": "success" if ok else "error", "message": message}
        if channel is not None:
            query["channel_id"] = channel.pk
        return HttpResponseRedirect(f"{target}?{urlencode(query)}")
    title = "Kết nối thành công" if ok else "Kết nối thất bại"
    html = (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title></head><body style="font-family:sans-serif;padding:16px">'
        f"<h1>{title}</h1><p>{escape(message)}</p></body></html>"
    )
    return HttpResponse(html, status=200 if ok else 400)


def zalo_site_verification(request, code):
    """Serves ``/zalo_verifier<code>.html`` for Zalo domain verification when configured."""
    expected = settings.ZALO_SITE_VERIFICATION_CODE
    if not expected or code != expected:
        raise Http404
    html = (
        "<!doctype html><html><head>"
        f'<meta property="zalo-platform-site-verification" content="{escape(expected)}" />'
        "</head><body>Zalo site verification</body></html>"
    )
    return HttpResponse(html)
