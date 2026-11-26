"""Real-time notifications to the admin UI over WebSocket (Blueprint D4).

- The browser asks ``POST /api/notifications/ticket/`` (JWT) for a 60-second signed ticket,
  then opens ``/ws/notifications/?ticket=...``. The JWT itself never goes in a URL.
- Each connection joins its tenant's group only, so tenant A never hears tenant B.
- Payloads are small hints (ids, counts, a display name) so the UI refreshes what changed;
  message text is never broadcast.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core import signing
from django.db import transaction

logger = logging.getLogger(__name__)

TICKET_SALT = "zalo-ai.ws-ticket"
TICKET_MAX_AGE_SECONDS = 60


def tenant_group(tenant_id: int) -> str:
    return f"tenant-{tenant_id}"


def make_ticket(user) -> str:
    return signing.dumps({"u": user.pk, "t": user.tenant_id}, salt=TICKET_SALT)


def read_ticket(ticket: str) -> dict | None:
    try:
        return signing.loads(ticket, salt=TICKET_SALT, max_age=TICKET_MAX_AGE_SECONDS)
    except (signing.BadSignature, ValueError, TypeError):  # forged, expired or malformed
        return None


def _send(tenant_id: int, event: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(tenant_group(tenant_id), {"type": "notify", "event": event})
    except Exception:  # noqa: BLE001 - notifications are best effort, never break a request
        logger.warning("Could not broadcast %s to tenant %s", event.get("type"), tenant_id)


def notify(tenant_id: int | None, event_type: str, **data) -> None:
    """Broadcast after the current transaction commits (nothing for rolled-back data)."""
    if not tenant_id:
        return
    event = {"type": event_type, **data}
    transaction.on_commit(lambda: _send(tenant_id, event))
