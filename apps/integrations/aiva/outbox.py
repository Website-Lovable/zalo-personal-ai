"""Enqueue outbound AIVA events (transactional outbox). Delivery: ``delivery.py``.

The row is written in the caller's transaction; delivery is scheduled on commit, so an
event is only ever sent for data that was really saved.

Guards (Kickoff 2.1 / 2.4):
- mode off  -> nothing is enqueued, nothing leaves the app;
- sandbox   -> only data_origin=demo; live -> only data_origin=real;
- contacts of a zalo_personal channel without consent are never shared with AIVA;
- live (Phase 6): the tenant must be activated for live (``live.can_enqueue``) and a contact
  must have a documented consent (status + source + time), except for ``consent.changed``.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from . import config, live
from .models import IntegrationAuditLog, IntegrationOutbox
from .schema import build_envelope

logger = logging.getLogger(__name__)


def _blocked(tenant, event_type: str, data_origin: str, reason: str) -> None:
    IntegrationAuditLog.objects.create(
        tenant=tenant,
        direction=IntegrationAuditLog.Direction.OUTBOUND,
        mode=config.get_mode(),
        event_type=event_type,
        data_origin=data_origin,
        success=False,
        detail=f"blocked: {reason}",
    )


def enqueue_event(
    tenant,
    event_type: str,
    payload: dict[str, Any],
    *,
    data_origin: str,
    contact=None,
    idempotency_key: str = "",
) -> IntegrationOutbox | None:
    """Queue one event. ``idempotency_key`` (e.g. ``lead.inbound:contact:12``) makes repeated
    calls for the same fact a no-op that returns the existing row."""
    if not config.is_enabled():
        logger.debug("AIVA integration off; skip %s", event_type)
        return None
    if data_origin not in config.allowed_data_origins():
        _blocked(
            tenant,
            event_type,
            data_origin,
            f"data_origin {data_origin} not allowed in mode {config.get_mode()}",
        )
        return None
    if contact is not None and contact.channel.is_personal and not contact.has_consent:
        _blocked(tenant, event_type, data_origin, "zalo_personal contact without consent")
        return None
    allowed, reason = live.can_enqueue(tenant)
    if not allowed:
        _blocked(tenant, event_type, data_origin, reason)
        return None
    if (
        contact is not None
        and config.get_mode() == config.LIVE
        and event_type != "consent.changed"
        and not contact.has_documented_consent
    ):
        _blocked(tenant, event_type, data_origin, "contact consent not documented")
        return None
    if not tenant.external_ref:
        _blocked(tenant, event_type, data_origin, "tenant has no external_ref")
        return None

    if idempotency_key:
        existing = IntegrationOutbox.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

    envelope = build_envelope(
        event_type=event_type,
        tenant_ref=tenant.external_ref,
        data_origin=data_origin,
        payload=payload,
    )
    with transaction.atomic():
        item = IntegrationOutbox.objects.create(
            tenant=tenant,
            event_id=envelope["event_id"],
            event_type=event_type,
            payload=envelope,
            data_origin=data_origin,
            idempotency_key=idempotency_key or f"{event_type}:{envelope['event_id']}",
        )
        IntegrationAuditLog.objects.create(
            tenant=tenant,
            direction=IntegrationAuditLog.Direction.OUTBOUND,
            mode=config.get_mode(),
            event_type=event_type,
            event_id=item.event_id,
            data_origin=data_origin,
            success=True,
            detail="enqueued",
        )
    _schedule_delivery(item.pk)
    return item


def _schedule_delivery(item_id: int) -> None:
    from .tasks import deliver_outbox_item

    def _send():
        try:
            deliver_outbox_item.delay(item_id)
        except Exception:  # noqa: BLE001 - broker down: the periodic sweep delivers it later
            logger.warning("Could not schedule AIVA outbox item %s; beat will retry", item_id)

    transaction.on_commit(_send)
