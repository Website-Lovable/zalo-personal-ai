"""Emergency stop (Blueprint C6 #7) and human takeover (C6 #8)."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.audit import log_action

from .models import AIControl


@transaction.atomic
def emergency_stop(tenant, *, user=None, reason: str = "", request=None) -> AIControl:
    """Stop every AI reply and draft for the tenant immediately.

    Replies already waiting for their natural delay re-check the switch right before sending,
    so nothing the AI wrote goes out after this call. People can still send by hand and
    approve drafts.
    """
    control = AIControl.objects.select_for_update().get(pk=AIControl.for_tenant(tenant).pk)
    control.emergency_stop = True
    control.stopped_at = timezone.now()
    control.stopped_by = user
    control.stop_reason = reason[:500]
    control.save()
    log_action(
        "ai.emergency_stop",
        tenant=tenant,
        user=user,
        obj=control,
        changes={"reason": reason[:500]},
        request=request,
    )
    return control


@transaction.atomic
def resume(tenant, *, user=None, request=None) -> AIControl:
    control = AIControl.objects.select_for_update().get(pk=AIControl.for_tenant(tenant).pk)
    control.emergency_stop = False
    control.resumed_at = timezone.now()
    control.save()
    log_action("ai.resumed", tenant=tenant, user=user, obj=control, request=request)
    return control


def take_over(conversation, *, user=None, minutes: int | None = None, request=None, auto=False):
    """A person handles the conversation; the AI stays silent until released or expiry."""
    now = timezone.now()
    if auto and conversation.takeover_active(now) and conversation.takeover_until is None:
        return conversation  # an open-ended manual takeover is already in place
    conversation.human_takeover = True
    conversation.takeover_by = user
    conversation.takeover_at = now
    conversation.takeover_until = now + timedelta(minutes=minutes) if minutes else None
    conversation.save(
        update_fields=[
            "human_takeover",
            "takeover_by",
            "takeover_at",
            "takeover_until",
            "updated_at",
        ]
    )
    log_action(
        "conversation.takeover_auto" if auto else "conversation.takeover",
        tenant=conversation.tenant,
        user=user,
        obj=conversation,
        changes={"minutes": minutes},
        request=request,
    )
    return conversation


def auto_take_over(conversation, *, user=None):
    """Staff replied by hand (in the app or directly in Zalo): pause the AI for a while."""
    minutes = settings.HUMAN_TAKEOVER_AUTO_MINUTES
    if minutes <= 0:
        return conversation
    return take_over(conversation, user=user, minutes=minutes, auto=True)


def release(conversation, *, user=None, request=None):
    conversation.human_takeover = False
    conversation.takeover_until = None
    conversation.save(update_fields=["human_takeover", "takeover_until", "updated_at"])
    log_action(
        "conversation.takeover_released",
        tenant=conversation.tenant,
        user=user,
        obj=conversation,
        request=request,
    )
    return conversation
