"""Producers of Zalo AI -> AIVA events (Kickoff 2.3), wired to model signals in ``apps.py``.

Rules applied here, on top of the outbox guards (mode, data_origin, tenant_ref):
- only contacts with a documented consent (status + source + time) are shared (Kickoff
  2.1); ``consent.changed`` is sent by the contact API itself and is the one event about a
  contact without consent;
- only summaries, intents and memory facts are sent, never message history;
- each fact is sent once (idempotency keys), summaries at most once per interval;
- a failure here never breaks the caller: work runs in a savepoint and errors are logged.
"""

from __future__ import annotations

import logging
from functools import wraps

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import config
from .outbox import enqueue_event

logger = logging.getLogger(__name__)

#: Model intent tags (apps.ai_engine.prompt) that hint at a sales opportunity.
OPPORTUNITY_INTENTS = {
    "price",
    "pricing",
    "quote",
    "budget",
    "booking",
    "purchase",
    "buy",
    "order",
    "course",
    "course_interest",
    "demo_request",
    "contract",
    "timeline",
}


def _safe(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not config.is_enabled():
            return None
        try:
            with transaction.atomic():
                return func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - integration must never break messaging
            logger.exception("AIVA event producer %s failed", func.__name__)
            return None

    return wrapper


def _contact_payload(contact) -> dict:
    payload = {"contact_ref": str(contact.pk)}
    if contact.crs_customer_id:
        payload["crs_customer_id"] = contact.crs_customer_id
    return payload


def _shareable(contact) -> bool:
    return contact is not None and contact.has_documented_consent


@_safe
def lead_inbound(contact):
    """A consented contact wrote to us (first time we see them as a lead)."""
    if not _shareable(contact) or not contact.has_messaged_first:
        return None
    return enqueue_event(
        contact.tenant,
        "lead.inbound",
        {
            **_contact_payload(contact),
            "display_name": contact.display_name,
            "channel_type": contact.channel.channel_type,
            "consent_status": "granted",
            "segment": contact.segment or "unknown",
        },
        data_origin=contact.data_origin,
        contact=contact,
        idempotency_key=f"lead.inbound:contact:{contact.pk}",
    )


def recent_intents(conversation, limit: int = 20) -> list[str]:
    from apps.ai_engine.models import AIReplyLog

    seen: list[str] = []
    for intents in (
        AIReplyLog.objects.filter(conversation=conversation)
        .order_by("-created_at")
        .values_list("intents", flat=True)[:limit]
    ):
        for tag in intents or []:
            if tag not in seen:
                seen.append(tag)
    return seen[:10]


@_safe
def conversation_summary(short_term):
    """New rolling summary of a conversation (already masked by the memory service)."""
    conversation = short_term.conversation
    contact = conversation.contact
    if not _shareable(contact) or not short_term.context_summary:
        return None
    interval = max(1, settings.AIVA_SUMMARY_MIN_INTERVAL_MINUTES) * 60
    bucket = int(timezone.now().timestamp() // interval)
    return enqueue_event(
        conversation.tenant,
        "conversation.summary",
        {
            **_contact_payload(contact),
            "conversation_ref": str(conversation.pk),
            "summary": short_term.context_summary[:4000],
            "detected_needs": recent_intents(conversation),
        },
        data_origin=conversation.data_origin,
        contact=contact,
        idempotency_key=f"conversation.summary:{conversation.pk}:{bucket}",
    )


@_safe
def opportunity_suggested(run):
    """The model tagged an inbound message with buying signals."""
    signals = sorted({t for t in (run.intents or []) if t in OPPORTUNITY_INTENTS})
    contact = run.conversation.contact
    if not signals or not _shareable(contact):
        return None
    payload = {**_contact_payload(contact), "signals": signals}
    if run.confidence is not None:
        payload["confidence"] = max(0.0, min(1.0, float(run.confidence)))
    return enqueue_event(
        run.tenant,
        "opportunity.suggested",
        payload,
        data_origin=run.data_origin,
        contact=contact,
        idempotency_key=f"opportunity.suggested:message:{run.inbound_message_id}",
    )


def _handoff_reason(approval) -> str:
    """Map checker reason codes (apps.permissions.checker) to the schema's reasons."""
    reasons = [str(r) for r in approval.reasons or []]
    if "handoff_requested" in reasons:
        return "customer_asked_human"
    if any(r.startswith(("sensitive:", "output:sensitive:", "avoid:")) for r in reasons):
        return "sensitive_topic"
    return "ai_uncertain"


@_safe
def handoff_requested(approval):
    """An escalation (a person must handle this conversation) was created."""
    contact = approval.contact
    if not _shareable(contact):
        return None
    payload = {**_contact_payload(contact), "reason": _handoff_reason(approval)}
    staff = approval.owner_staff_ref or contact.owner_staff_ref
    if staff:
        payload["owner_staff_ref"] = staff
    return enqueue_event(
        approval.tenant,
        "handoff.requested",
        payload,
        data_origin=approval.data_origin,
        contact=contact,
        idempotency_key=f"handoff.requested:approval:{approval.pk}",
    )


@_safe
def profile_update_suggested(memory):
    """An important long-term fact about a CRS customer; CRS decides whether to apply it."""
    contact = memory.contact
    if (
        not _shareable(contact)
        or not contact.crs_customer_id
        or memory.importance < settings.AIVA_PROFILE_SUGGESTION_MIN_IMPORTANCE
    ):
        return None
    return enqueue_event(
        memory.tenant,
        "profile.update_suggested",
        {
            **_contact_payload(contact),
            "suggested_changes": {
                "memory_fact": {"type": memory.memory_type, "content": memory.content[:1000]}
            },
        },
        data_origin=memory.data_origin,
        contact=contact,
        idempotency_key=f"profile.update_suggested:memory:{memory.pk}",
    )
