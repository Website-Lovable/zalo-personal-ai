"""Celery tasks for Zalo channels (webhook processing, token refresh, sync, polling)."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from .models import Channel, ChannelType, WebhookEvent

logger = logging.getLogger(__name__)

MAX_EVENT_ATTEMPTS = 5


@shared_task(bind=True, max_retries=MAX_EVENT_ATTEMPTS, ignore_result=True)
def process_webhook_event(self, event_id: int):
    from .services import process_event

    event = WebhookEvent.objects.select_related("channel").filter(pk=event_id).first()
    if event is None:
        return None
    event = process_event(event)
    if event.status == WebhookEvent.Status.FAILED and event.attempts < MAX_EVENT_ATTEMPTS:
        raise self.retry(countdown=min(600, 10 * 2**event.attempts))
    return event.status


@shared_task(ignore_result=True)
def refresh_expiring_oa_tokens():
    from .services import refresh_expiring_oa_tokens as run

    return run()


@shared_task(ignore_result=True)
def sync_channel_contacts(channel_id: int):
    from .services import sync_oa_contacts

    channel = Channel.objects.filter(pk=channel_id, is_active=True).first()
    if channel is None:
        return None
    return sync_oa_contacts(channel)


@shared_task(ignore_result=True)
def poll_bot_channels():
    """Beat job for bots in polling delivery (useful without a public HTTPS URL)."""
    from .providers.http import ZaloAPIError
    from .services import poll_bot_once

    if settings.ZALO_CHANNEL_BACKEND != "real":
        return 0
    total = 0
    channels = Channel.objects.filter(
        channel_type=ChannelType.ZALO_BOT,
        is_active=True,
        status=Channel.Status.ACTIVE,
        config__delivery="polling",
    )
    for channel in channels:
        lock = f"zalo-bot-poll-{channel.pk}"
        if not cache.add(lock, 1, timeout=settings.ZALO_BOT_POLL_TIMEOUT_SECONDS + 30):
            continue  # a previous poll for this bot is still running
        try:
            total += poll_bot_once(channel)
        except ZaloAPIError as exc:
            logger.warning("Polling bot channel %s failed: %s", channel.pk, exc)
        finally:
            cache.delete(lock)
    return total
