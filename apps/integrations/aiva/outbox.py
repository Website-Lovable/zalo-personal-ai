"""Enqueue outbound AIVA events (transactional outbox). Delivery worker: Phase 3.5.

Guards (Kickoff 2.1 / 2.4):
- mode off  -> nothing is enqueued, nothing leaves the app;
- sandbox   -> only data_origin=demo; live -> only data_origin=real;
- contacts of a zalo_personal channel without consent are never shared with AIVA.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from . import config
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
    tenant, event_type: str, payload: dict[str, Any], *, data_origin: str, contact=None
) -> IntegrationOutbox | None:
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
    if not tenant.external_ref:
        _blocked(tenant, event_type, data_origin, "tenant has no external_ref")
        return None

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
            idempotency_key=f"{event_type}:{envelope['event_id']}",
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
    return item
