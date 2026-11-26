"""Phase 5: readiness, Prometheus metrics, heartbeat, JSON logs."""

import json
import logging

import pytest
from django.core.cache import cache

from apps.ai_engine.models import AIControl
from common import observability
from common.logging import JSONFormatter
from common.tasks import heartbeat
from tests.factories import ApprovalFactory, ChannelFactory

pytestmark = pytest.mark.django_db


def test_readyz_ok_and_failure(client, monkeypatch):
    response = client.get("/readyz/")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "skipped"},
    }
    monkeypatch.setattr(observability, "check_database", lambda: False)
    response = client.get("/readyz/")
    assert response.status_code == 503 and response.json()["checks"]["database"] == "fail"


def test_redis_check_used_outside_eager_mode(settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = False

    class Down:
        def ping(self):
            raise ConnectionError("down")

        def llen(self, key):
            raise ConnectionError("down")

    class Up:
        def ping(self):
            return True

        def llen(self, key):
            return 7

    monkeypatch.setattr(observability, "_redis", lambda: Down())
    assert observability.check_redis() is False
    monkeypatch.setattr(observability, "_redis", lambda: Up())
    assert observability.check_redis() is True
    settings.METRICS_TOKEN = "t" * 32
    assert "zalo_ai_celery_queue_length 7" in observability.collect_metrics()


def test_database_check_failure_is_reported(monkeypatch):
    class Broken:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(observability.connection, "cursor", lambda: Broken())
    assert observability.check_database() is False
    text = observability.collect_metrics()
    assert "zalo_ai_database_up 0" in text and "zalo_ai_channels" not in text


def test_metrics_disabled_without_token(client, settings):
    settings.METRICS_TOKEN = ""
    assert client.get("/metrics/").status_code == 404


def test_metrics_requires_token(client, settings):
    settings.METRICS_TOKEN = "s" * 40
    assert client.get("/metrics/").status_code == 401
    bad = client.get("/metrics/", HTTP_AUTHORIZATION="Bearer wrong")
    assert bad.status_code == 401


def test_metrics_content_has_no_tenant_data(client, settings, tenant_a, tenant_b):
    settings.METRICS_TOKEN = "s" * 40
    ChannelFactory(tenant=tenant_a, channel_type="zalo_oa", status="active")
    ChannelFactory(tenant=tenant_b, channel_type="zalo_oa", status="restricted")
    ApprovalFactory(tenant=tenant_a)
    AIControl.objects.create(tenant=tenant_b, emergency_stop=True)
    response = client.get("/metrics/", HTTP_AUTHORIZATION="Bearer " + "s" * 40)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    text = response.content.decode()
    assert 'zalo_ai_channels{status="active",type="zalo_oa"} 1' in text
    assert 'zalo_ai_channels{status="restricted",type="zalo_oa"} 1' in text
    assert "zalo_ai_approvals_pending 1" in text
    assert "zalo_ai_emergency_stops 1" in text
    assert 'zalo_ai_aiva_outbox{status="dead"} 0' in text
    assert "zalo_ai_celery_heartbeat_age_seconds NaN" in text
    assert 'zalo_ai_llm_tokens_last_hour{kind="input"} 0' in text
    for leak in (tenant_a.name, tenant_b.name, str(tenant_a.pk) + '"'):
        assert leak not in text
    for line in text.splitlines():  # valid exposition format
        assert line.startswith("#") or line.startswith("zalo_ai_"), line


def test_heartbeat_task_updates_age():
    cache.clear()
    assert observability.heartbeat_age_seconds() is None
    assert heartbeat() is True
    assert observability.heartbeat_age_seconds() < 5


def test_label_escaping_and_numbers():
    assert observability._escape('a"b\\c\nd') == 'a\\"b\\\\c\\nd'
    assert observability._number(None) == "NaN"
    assert observability._number(True) == "1"
    assert observability._number(1.5) == "1.5"


def test_json_log_formatter():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "Lỗi %s", ("gửi",), None)
    record.status_code = 500
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    data = json.loads(JSONFormatter().format(record))
    assert data["message"] == "Lỗi gửi" and data["level"] == "ERROR" and data["status"] == 500
    assert "ValueError" in data["exc"]
