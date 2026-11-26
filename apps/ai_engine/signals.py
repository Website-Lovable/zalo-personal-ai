"""Hook the AI pipeline onto stored inbound messages (Phase 2 signal)."""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.dispatch import receiver

from apps.conversations.inbound import inbound_message_received


@receiver(inbound_message_received, dispatch_uid="ai_engine.schedule_reply")
def schedule_reply(sender, message, conversation, channel, **kwargs):
    if not settings.AI_ENGINE_ENABLED:
        return
    from .tasks import process_inbound_message

    delay = max(0, settings.DEFAULT_AI_RESPONSE_DELAY_SECONDS)
    transaction.on_commit(
        lambda: process_inbound_message.apply_async((message.pk,), countdown=delay)
    )
