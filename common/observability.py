"""Health, readiness and Prometheus metrics (Phase 5: monitoring & alerting).

- ``/healthz/``  liveness: the process answers (no dependencies checked).
- ``/readyz/``   readiness: database and Redis reachable; 503 otherwise.
- ``/metrics/``  Prometheus text format, bearer token METRICS_TOKEN; 404 while it is unset.

Metrics are platform-wide aggregates computed at scrape time from the database, so they are
the same whichever web process answers, and they never carry tenant or customer labels.
HTTP request rates and latencies come from Caddy's own metrics (deploy/Caddyfile).
"""

from __future__ import annotations

import hmac
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Min, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "zalo-ai:celery-heartbeat"
RECENT = timedelta(minutes=15)


# --- dependency checks -----------------------------------------------------------------


def check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        logger.exception("readiness: database check failed")
        return False


def _redis():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)


def check_redis() -> bool | None:
    """None = Redis not needed by this configuration (no Celery broker in eager mode)."""
    if settings.CELERY_TASK_ALWAYS_EAGER and settings.CACHE_BACKEND != "redis":
        return None
    try:
        return bool(_redis().ping())
    except Exception:  # noqa: BLE001
        logger.exception("readiness: redis check failed")
        return False


def heartbeat_age_seconds() -> float | None:
    beat = cache.get(HEARTBEAT_KEY)
    return None if beat is None else max(0.0, time.time() - float(beat))


@never_cache
@require_GET
def readyz(request):
    checks = {"database": check_database(), "redis": check_redis()}
    ok = all(v is not False for v in checks.values())
    body = {
        "status": "ok" if ok else "unavailable",
        "checks": {
            k: ("skipped" if v is None else ("ok" if v else "fail")) for k, v in checks.items()
        },
    }
    return JsonResponse(body, status=200 if ok else 503)


# --- Prometheus ------------------------------------------------------------------------


class _Writer:
    def __init__(self):
        self.lines: list[str] = []

    def metric(self, name: str, kind: str, help_text: str, samples):
        self.lines.append(f"# HELP {name} {help_text}")
        self.lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            label_text = ""
            if labels:
                inner = ",".join(f'{k}="{_escape(str(v))}"' for k, v in sorted(labels.items()))
                label_text = "{" + inner + "}"
            self.lines.append(f"{name}{label_text} {_number(value)}")

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace('"', r"\"").replace("\n", r"\n")


def _number(value) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, bool):
        return "1" if value else "0"
    return repr(float(value)) if isinstance(value, float) else str(int(value))


def _counts(qs, field: str, choices) -> list[tuple[dict, int]]:
    found = {row[field]: row["n"] for row in qs.values(field).annotate(n=Count("id"))}
    return [({field: value}, found.get(value, 0)) for value in choices]


def _age(oldest) -> float:
    return 0.0 if oldest is None else (timezone.now() - oldest).total_seconds()


def collect_metrics() -> str:
    from apps.ai_engine.models import AIControl, AIReplyLog, LLMUsage
    from apps.channels.models import Channel, ChannelType, WebhookEvent
    from apps.conversations.models import Message
    from apps.integrations.aiva.models import IntegrationOutbox
    from apps.permissions.models import ApprovalQueue

    now = timezone.now()
    since = now - RECENT
    out = _Writer()
    db_ok = check_database()
    redis_ok = check_redis()
    out.metric("zalo_ai_up", "gauge", "The web process answers.", [({}, 1)])
    out.metric("zalo_ai_database_up", "gauge", "Database reachable.", [({}, db_ok)])
    out.metric("zalo_ai_redis_up", "gauge", "Redis reachable (NaN = not used).", [({}, redis_ok)])
    out.metric(
        "zalo_ai_celery_heartbeat_age_seconds",
        "gauge",
        "Seconds since Celery beat + worker last ran the heartbeat task (NaN = never).",
        [({}, heartbeat_age_seconds())],
    )
    if redis_ok:
        try:
            depth = _redis().llen("celery")
        except Exception:  # noqa: BLE001
            depth = None
        out.metric("zalo_ai_celery_queue_length", "gauge", "Tasks waiting in Redis.", [({}, depth)])
    if not db_ok:
        return out.render()

    channels = Channel.objects.filter(is_active=True)
    found = {
        (r["channel_type"], r["status"]): r["n"]
        for r in channels.values("channel_type", "status").annotate(n=Count("id"))
    }
    out.metric(
        "zalo_ai_channels",
        "gauge",
        "Active channels by type and status.",
        [
            ({"type": t, "status": s}, found.get((t, s), 0))
            for t in ChannelType.values
            for s in Channel.Status.values
        ],
    )
    out.metric(
        "zalo_ai_emergency_stops",
        "gauge",
        "Tenants whose AI is stopped by the emergency switch.",
        [({}, AIControl.objects.filter(emergency_stop=True).count())],
    )
    out.metric(
        "zalo_ai_personal_emergency_stop",
        "gauge",
        "Platform kill switch for personal Zalo sends (ZALO_PERSONAL_EMERGENCY_STOP).",
        [({}, settings.ZALO_PERSONAL_EMERGENCY_STOP)],
    )

    pending = ApprovalQueue.objects.filter(status="pending")
    out.metric(
        "zalo_ai_approvals_pending", "gauge", "Drafts waiting for a human.", [({}, pending.count())]
    )
    out.metric(
        "zalo_ai_approvals_pending_oldest_age_seconds",
        "gauge",
        "Age of the oldest pending draft.",
        [({}, _age(pending.aggregate(m=Min("created_at"))["m"]))],
    )

    out.metric(
        "zalo_ai_aiva_outbox",
        "gauge",
        "AIVA outbox rows by status.",
        _counts(IntegrationOutbox.objects.all(), "status", IntegrationOutbox.Status.values),
    )
    waiting = IntegrationOutbox.objects.filter(status__in=["pending", "failed"])
    out.metric(
        "zalo_ai_aiva_outbox_oldest_waiting_age_seconds",
        "gauge",
        "Age of the oldest undelivered AIVA event.",
        [({}, _age(waiting.aggregate(m=Min("created_at"))["m"]))],
    )

    out.metric(
        "zalo_ai_webhook_events_recent",
        "gauge",
        "Zalo webhook/polling events received in the last 15 minutes, by status.",
        _counts(
            WebhookEvent.objects.filter(created_at__gte=since), "status", WebhookEvent.Status.values
        ),
    )
    out.metric(
        "zalo_ai_messages_recent",
        "gauge",
        "Messages stored in the last 15 minutes, by direction.",
        _counts(
            Message.objects.filter(created_at__gte=since), "direction", Message.Direction.values
        ),
    )
    out.metric(
        "zalo_ai_ai_runs_recent",
        "gauge",
        "AI pipeline runs in the last 15 minutes, by outcome.",
        _counts(
            AIReplyLog.objects.filter(created_at__gte=since), "outcome", AIReplyLog.Outcome.values
        ),
    )
    usage = LLMUsage.objects.filter(created_at__gte=now - timedelta(hours=1)).aggregate(
        i=Sum("input_tokens"), o=Sum("output_tokens")
    )
    out.metric(
        "zalo_ai_llm_tokens_last_hour",
        "gauge",
        "Claude tokens used in the last hour (all tenants).",
        [({"kind": "input"}, usage["i"] or 0), ({"kind": "output"}, usage["o"] or 0)],
    )
    return out.render()


@never_cache
@require_GET
def metrics(request):
    token = settings.METRICS_TOKEN
    if not token:
        raise Http404
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        return HttpResponse(status=401, headers={"WWW-Authenticate": "Bearer"})
    return HttpResponse(collect_metrics(), content_type="text/plain; version=0.0.4")
