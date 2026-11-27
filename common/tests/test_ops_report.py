"""Measured daily operating figures (common.ops_report): compute, store, pull, push."""

import json
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from apps.ai_engine.models import AIReplyLog, LLMUsage
from common import ops_report
from common.models import OpsDailySnapshot
from common.tasks import compute_ops_snapshots
from tests.factories import ApprovalFactory, ChannelFactory, MessageFactory

pytestmark = pytest.mark.django_db
SECRET = "s" * 40


def _run(message, outcome):
    return AIReplyLog.objects.create(
        tenant=message.tenant,
        conversation=message.conversation,
        inbound_message=message,
        outcome=outcome,
        data_origin=message.data_origin,
    )


@pytest.fixture
def activity(tenant_a):
    channel = ChannelFactory(tenant=tenant_a, status="active", data_origin="real")
    m1 = MessageFactory(conversation__contact__channel=channel, data_origin="real")
    m2 = MessageFactory(conversation=m1.conversation, data_origin="real")
    _run(m1, "sent")
    _run(m2, "need_approval")
    ApprovalFactory(message=m2, data_origin="real", status="approved", reviewed_at=timezone.now())
    m3 = MessageFactory(conversation=m1.conversation, data_origin="real", direction="outbound")
    ApprovalFactory(message=m3, data_origin="real")  # still pending
    LLMUsage.objects.create(
        tenant=tenant_a, provider="mock", model="m", input_tokens=100, output_tokens=20
    )
    # demo data must never be counted
    demo = MessageFactory(conversation__contact__channel__tenant=tenant_a, data_origin="demo")
    _run(demo, "sent")
    return channel


def test_compute_counts_real_data_only(activity):
    data = ops_report.compute(timezone.localdate())
    assert data["messages"] == {"inbound": 2, "outbound": 1}
    assert data["ai"]["runs"] == 2 and data["ai"]["outcomes"]["sent"] == 1
    assert data["ai"]["auto_reply_rate"] == 0.5
    assert data["approvals"]["decided"]["approved"] == 1
    assert data["approvals"]["created"] == 2 and data["approvals"]["pending_now"] == 1
    assert data["channels"]["zalo_oa"]["active"] == 1
    assert data["llm_tokens"] == {"input": 100, "output": 20}
    assert data["errors"] == {"ai_failed": 0, "aiva_dead_events": 0}
    text = json.dumps(data, ensure_ascii=False)
    assert "Xin chào" not in text and "Tenant A" not in text  # no content, no tenant names


def test_empty_day_has_no_rate(db):
    data = ops_report.compute(timezone.localdate() - timedelta(days=10))
    assert data["ai"]["runs"] == 0 and data["ai"]["auto_reply_rate"] is None


def test_refresh_keeps_one_row_per_day(activity):
    ops_report.refresh()
    ops_report.refresh()
    assert OpsDailySnapshot.objects.count() == 2  # yesterday + today
    today = OpsDailySnapshot.objects.get(date=timezone.localdate())
    assert today.data["messages"]["inbound"] == 2
    assert [s["date"] for s in ops_report.recent(7)][-1] == timezone.localdate().isoformat()


def test_task_without_push_url(activity, settings):
    settings.OPS_PUSH_URL = ""
    assert compute_ops_snapshots() == 2
    assert OpsDailySnapshot.objects.filter(pushed_at__isnull=True).count() == 2


def test_push_is_signed_and_recorded(activity, settings):
    settings.OPS_PUSH_URL = "https://progress.example/ingest"
    settings.OPS_PUSH_SECRET = SECRET
    seen = {}

    def handler(request):
        seen["body"] = request.content
        seen["headers"] = request.headers
        return httpx.Response(204)

    rows = ops_report.refresh()
    assert ops_report.push(rows, transport=httpx.MockTransport(handler))
    timestamp = int(seen["headers"]["X-Timestamp"])
    assert seen["headers"]["X-Signature"] == ops_report.sign(SECRET, seen["body"], timestamp)
    assert len(json.loads(seen["body"])["snapshots"]) == 2
    assert all(r.pushed_at and not r.push_error for r in OpsDailySnapshot.objects.all())


def test_push_failures_are_recorded(activity, settings):
    settings.OPS_PUSH_URL = "https://progress.example/ingest"
    settings.OPS_PUSH_SECRET = SECRET
    rows = ops_report.refresh()
    assert not ops_report.push(rows, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert OpsDailySnapshot.objects.first().push_error == "HTTP 500"

    def boom(request):
        raise httpx.ConnectError("down")

    assert not ops_report.push(rows, transport=httpx.MockTransport(boom))
    assert OpsDailySnapshot.objects.first().push_error.startswith("network")


def test_snapshot_endpoint_needs_metrics_token(activity, client, settings):
    settings.METRICS_TOKEN = ""
    assert client.get("/api/ops/snapshots/").status_code == 404
    settings.METRICS_TOKEN = "m" * 40
    assert client.get("/api/ops/snapshots/").status_code == 401
    ops_report.refresh()
    auth = {"HTTP_AUTHORIZATION": "Bearer " + "m" * 40}
    body = client.get("/api/ops/snapshots/?days=2", **auth).json()
    assert len(body["snapshots"]) == 2
    assert client.get("/api/ops/snapshots/?days=x", **auth).status_code == 200


def test_push_url_needs_https_and_strong_secret(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    import config.settings.base as base

    monkeypatch.setenv("OPS_PUSH_URL", "http://insecure")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base)
    monkeypatch.delenv("OPS_PUSH_URL")
    importlib.reload(base)
