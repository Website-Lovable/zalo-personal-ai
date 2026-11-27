"""Outbox delivery worker (Kickoff 2.3 / 2.4): send, retry with backoff, dead-letter.

- Each row is locked (``SELECT ... FOR UPDATE SKIP LOCKED``) while it is sent, so two
  workers never send the same event twice; AIVA also dedupes on ``event_id``.
- Mode is checked again at send time: switching to ``off`` freezes the queue (rows stay
  pending). A row whose ``data_origin`` is not allowed in the current mode is never sent and
  stays frozen (sandbox never carries real data, live never carries demo data). Phase 6:
  frozen instead of dead-lettered, so a rollback from live to sandbox loses no real event.
- Live (Phase 6): only rows of tenants whose live enrollment is ``active`` are due, and
  nothing is due while the platform switch is paused (rollback levels 1 and 2).
- Every attempt is written to ``IntegrationAuditLog``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from common.exceptions import FeatureDisabledError

from . import config, live
from .client import AIVAClient, SendResult, get_aiva_client
from .models import IntegrationAuditLog, IntegrationOutbox

logger = logging.getLogger(__name__)
Status = IntegrationOutbox.Status


def backoff_seconds(attempts: int) -> int:
    base = settings.AIVA_OUTBOX_BACKOFF_BASE_SECONDS
    return int(min(base * (2 ** max(0, attempts - 1)), settings.AIVA_OUTBOX_BACKOFF_MAX_SECONDS))


def _due(now) -> Q:
    due = (
        Q(status__in=[Status.PENDING, Status.FAILED])
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        & Q(data_origin__in=sorted(config.allowed_data_origins()))
    )
    if config.get_mode() == config.LIVE:
        if live.is_globally_paused():
            return Q(pk__in=[])
        due &= Q(tenant_id__in=live.active_tenant_ids())
    return due


def _audit(item: IntegrationOutbox, *, success: bool, result: SendResult | None, detail: str):
    IntegrationAuditLog.objects.create(
        tenant_id=item.tenant_id,
        direction=IntegrationAuditLog.Direction.OUTBOUND,
        mode=config.get_mode(),
        event_type=item.event_type,
        event_id=item.event_id,
        data_origin=item.data_origin,
        success=success,
        status_code=result.status_code if result else None,
        detail=detail[:1000],
    )


def _send_locked(item: IntegrationOutbox, client: AIVAClient, now) -> str:
    item.attempts += 1
    try:
        result = client.send_event(item.payload)
    except FeatureDisabledError as exc:
        result = SendResult(ok=False, detail=f"config: {exc}")
    except Exception as exc:  # noqa: BLE001 - schema drift or a bug: keep the row, retry
        logger.exception("AIVA send failed for outbox %s", item.pk)
        result = SendResult(ok=False, detail=f"error: {exc!r}"[:500])

    if result.ok:
        item.status = Status.SENT
        item.sent_at = now
        item.last_error = ""
        item.next_attempt_at = None
        _audit(item, success=True, result=result, detail=result.detail or "delivered")
    elif not result.retryable or item.attempts >= settings.AIVA_OUTBOX_MAX_ATTEMPTS:
        item.status = Status.DEAD
        item.last_error = result.detail or f"HTTP {result.status_code}"
        item.next_attempt_at = None
        _audit(item, success=False, result=result, detail=f"dead: {item.last_error}")
    else:
        item.status = Status.FAILED
        item.last_error = result.detail or f"HTTP {result.status_code}"
        item.next_attempt_at = now + timedelta(seconds=backoff_seconds(item.attempts))
        _audit(item, success=False, result=result, detail=f"retry: {item.last_error}")
    item.save()
    return item.status


def deliver_one(item_id: int, *, client: AIVAClient | None = None) -> str | None:
    """Deliver one row if it is due. Returns its new status (None when not processed)."""
    if not config.is_enabled():
        return None
    now = timezone.now()
    with transaction.atomic():
        item = (
            IntegrationOutbox.objects.select_for_update(skip_locked=True)
            .filter(_due(now), pk=item_id)
            .first()
        )
        if item is None:
            return None
        return _send_locked(item, client or _client(), now)


def deliver_due(*, limit: int | None = None, client: AIVAClient | None = None) -> dict[str, int]:
    """Deliver every due row (oldest first). Used by the periodic beat task."""
    counts = {"sent": 0, "failed": 0, "dead": 0}
    live.record_mode()
    if not config.is_enabled():
        return counts
    client = client or _client()
    limit = limit or settings.AIVA_OUTBOX_BATCH_SIZE
    ids = list(
        IntegrationOutbox.objects.filter(_due(timezone.now()))
        .order_by("created_at", "id")
        .values_list("pk", flat=True)[:limit]
    )
    for item_id in ids:
        status = deliver_one(item_id, client=client)
        if status in counts:
            counts[status] += 1
    return counts


def requeue(item: IntegrationOutbox) -> IntegrationOutbox:
    """Put a dead/failed row back in the queue (admin action)."""
    item.status = Status.PENDING
    item.attempts = 0
    item.next_attempt_at = None
    item.save(update_fields=["status", "attempts", "next_attempt_at", "updated_at"])
    return item


def _client() -> AIVAClient:
    try:
        return get_aiva_client()
    except FeatureDisabledError as exc:
        from .client import DisabledAIVAClient

        return DisabledAIVAClient(str(exc))
