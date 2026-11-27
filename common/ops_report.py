"""Automatic, measured operating figures (no manual estimates).

Every hour ``common.tasks.compute_ops_snapshots`` recomputes today and yesterday into
``OpsDailySnapshot`` rows. Figures are platform-wide counts of REAL data only
(``data_origin=real``; demo/seed data is never counted), and never contain tenant names,
customer data or message content.

Two ways out, both off until configured:
- pull: ``GET /api/ops/snapshots/?days=30`` with ``Authorization: Bearer <METRICS_TOKEN>``;
- push: when ``OPS_PUSH_URL`` is set, each hourly run POSTs the latest snapshots there,
  signed ``X-Signature: sha256=<hex>`` = HMAC-SHA256(``OPS_PUSH_SECRET``,
  ``f"{X-Timestamp}." + body``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import date, datetime, timedelta

import httpx
from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import OpsDailySnapshot

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
REAL = "real"


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    return start, start + timedelta(days=1)


def _by(queryset, field: str, values) -> dict[str, int]:
    found = dict(queryset.values_list(field).annotate(n=Count("id")).values_list(field, "n"))
    return {value: found.get(value, 0) for value in values}


def compute(day: date) -> dict:
    """Figures for one local calendar day (settings.TIME_ZONE)."""
    from apps.accounts.models import Tenant
    from apps.ai_engine.models import AIControl, AIReplyLog, LLMUsage
    from apps.channels.models import Channel, ChannelType
    from apps.conversations.models import Message
    from apps.integrations.aiva.models import IntegrationOutbox
    from apps.permissions.models import ApprovalQueue

    start, end = _day_bounds(day)
    in_day = Q(created_at__gte=start, created_at__lt=end)

    messages = Message.objects.filter(in_day, data_origin=REAL)
    inbound = messages.filter(direction=Message.Direction.INBOUND).count()
    outbound = messages.filter(direction=Message.Direction.OUTBOUND).count()

    runs = AIReplyLog.objects.filter(in_day, data_origin=REAL)
    outcomes = _by(runs, "outcome", AIReplyLog.Outcome.values)
    total_runs = sum(outcomes.values())
    auto_sent = outcomes[AIReplyLog.Outcome.SENT]

    approvals = ApprovalQueue.objects.filter(data_origin=REAL)
    decided = _by(
        approvals.filter(reviewed_at__gte=start, reviewed_at__lt=end),
        "status",
        ["approved", "modified", "rejected"],
    )
    created = approvals.filter(in_day).count()

    channels = Channel.objects.filter(data_origin=REAL)
    by_type = {
        kind: _by(channels.filter(channel_type=kind), "status", Channel.Status.values)
        for kind in ChannelType.values
    }

    usage = LLMUsage.objects.filter(in_day).aggregate(i=Sum("input_tokens"), o=Sum("output_tokens"))
    outbox = IntegrationOutbox.objects.filter(data_origin=REAL)

    return {
        "schema_version": SCHEMA_VERSION,
        "date": day.isoformat(),
        "data_origin": REAL,
        "messages": {"inbound": inbound, "outbound": outbound},
        "ai": {
            "runs": total_runs,
            "outcomes": outcomes,
            # share of inbound messages the AI answered on its own, without a person
            "auto_reply_rate": round(auto_sent / total_runs, 4) if total_runs else None,
        },
        "approvals": {
            "created": created,
            "decided": decided,
            "pending_now": approvals.filter(status=ApprovalQueue.Status.PENDING).count(),
        },
        "errors": {
            "ai_failed": outcomes[AIReplyLog.Outcome.FAILED],
            "aiva_dead_events": outbox.filter(in_day, status="dead").count(),
        },
        "channels": by_type,
        "tenants": {
            "active": Tenant.objects.filter(is_active=True).count(),
            "emergency_stopped": AIControl.objects.filter(emergency_stop=True).count(),
        },
        "llm_tokens": {"input": usage["i"] or 0, "output": usage["o"] or 0},
    }


def refresh(now: datetime | None = None) -> list[OpsDailySnapshot]:
    """Recompute today and yesterday (yesterday becomes final after midnight)."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    rows = []
    for day in (today - timedelta(days=1), today):
        row, _ = OpsDailySnapshot.objects.update_or_create(
            date=day, defaults={"data": compute(day), "computed_at": now}
        )
        rows.append(row)
    return rows


def recent(days: int = 30) -> list[dict]:
    days = max(1, min(days, 366))
    since = timezone.localdate() - timedelta(days=days - 1)
    return [
        {**row.data, "computed_at": row.computed_at.isoformat()}
        for row in OpsDailySnapshot.objects.filter(date__gte=since).order_by("date")
    ]


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def push(rows: list[OpsDailySnapshot], *, transport: httpx.BaseTransport | None = None) -> bool:
    """POST the given snapshots to OPS_PUSH_URL. Returns False when off or on failure."""
    url, secret = settings.OPS_PUSH_URL, settings.OPS_PUSH_SECRET
    if not url:
        return False
    body = json.dumps(
        {"source": "zalo-personal-ai", "snapshots": [r.data for r in rows]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Timestamp": str(timestamp),
        "X-Signature": sign(secret, body, timestamp),
    }
    error = ""
    try:
        with httpx.Client(timeout=10, transport=transport) as client:
            response = client.post(url, content=body, headers=headers)
        if not 200 <= response.status_code < 300:
            error = f"HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        error = f"network: {exc.__class__.__name__}"
    now = timezone.now()
    for row in rows:
        row.push_error = error
        if not error:
            row.pushed_at = now
        row.save(update_fields=["push_error", "pushed_at"])
    if error:
        logger.warning("ops snapshot push failed: %s", error)
    return not error
