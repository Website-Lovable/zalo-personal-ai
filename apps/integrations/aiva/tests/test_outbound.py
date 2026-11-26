"""Phase 3.5: HTTP client (signed webhooks) and outbox delivery worker."""

import importlib
import json
from datetime import timedelta

import httpx
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.integrations.aiva import delivery
from apps.integrations.aiva.client import (
    SANDBOX_MOCK,
    DisabledAIVAClient,
    HTTPAIVAClient,
    MockAIVAClient,
    get_aiva_client,
)
from apps.integrations.aiva.models import IntegrationAuditLog, IntegrationOutbox
from apps.integrations.aiva.outbox import enqueue_event
from apps.integrations.aiva.schema import build_envelope
from apps.integrations.aiva.signing import verify_signature
from common.exceptions import FeatureDisabledError
from conftest import auth_client
from tests.factories import UserFactory

from .conftest import OUTBOUND_SECRET

PAYLOAD = {"contact_ref": "1", "consent_status": "granted"}


def _envelope():
    return build_envelope(
        event_type="consent.changed", tenant_ref="crs-a", data_origin="demo", payload=PAYLOAD
    )


# --- Client selection ----------------------------------------------------------------


def test_client_selection(settings):
    settings.AIVA_INTEGRATION_MODE = "off"
    assert isinstance(get_aiva_client(), DisabledAIVAClient)
    settings.AIVA_INTEGRATION_MODE = "sandbox"
    settings.AIVA_CLIENT = "mock"
    assert get_aiva_client() is SANDBOX_MOCK
    settings.AIVA_CLIENT = "http"
    settings.AIVA_BASE_URL = "https://aiva.test"
    settings.AIVA_WEBHOOK_SECRET = OUTBOUND_SECRET
    assert isinstance(get_aiva_client(), HTTPAIVAClient)
    settings.AIVA_BASE_URL = ""
    with pytest.raises(FeatureDisabledError):
        get_aiva_client()
    # live: refused without the owner's approval, and never with the mock
    settings.AIVA_INTEGRATION_MODE = "live"
    settings.AIVA_LIVE_APPROVED = False
    assert isinstance(get_aiva_client(), DisabledAIVAClient)
    settings.AIVA_LIVE_APPROVED = True
    settings.AIVA_CLIENT = "mock"
    assert isinstance(get_aiva_client(), DisabledAIVAClient)


def test_live_mode_refused_at_startup_without_approval(monkeypatch):
    import config.settings.base as base

    monkeypatch.setenv("AIVA_INTEGRATION_MODE", "live")
    monkeypatch.delenv("AIVA_LIVE_APPROVED", raising=False)
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base)
    monkeypatch.setenv("AIVA_INTEGRATION_MODE", "sandbox")
    monkeypatch.setenv("AIVA_CLIENT", "carrier-pigeon")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base)
    monkeypatch.setenv("AIVA_INTEGRATION_MODE", "off")
    monkeypatch.setenv("AIVA_CLIENT", "http")
    importlib.reload(base)


def test_http_client_requires_secret():
    with pytest.raises(FeatureDisabledError):
        HTTPAIVAClient(base_url="https://aiva.test", secret="")


# --- HTTP client: what AIVA receives ------------------------------------------------


class FakeAIVA:
    """httpx transport playing AIVA: checks the signature like AIVA must."""

    def __init__(self, status=202, raise_error=None):
        self.status = status
        self.raise_error = raise_error
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.raise_error:
            raise self.raise_error
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"status": "ok"})
        verify_signature(
            OUTBOUND_SECRET,
            request.content,
            request.headers["X-Signature"],
            request.headers["X-Timestamp"],
        )
        return httpx.Response(self.status, text="nope" if self.status >= 400 else "")


def _http(fake, **kw):
    return HTTPAIVAClient(
        base_url="https://aiva.test/",
        secret=OUTBOUND_SECRET,
        token="aiva-issued-token",
        events_path="/api/integration/zalo-ai/v1/events",
        transport=httpx.MockTransport(fake),
        **kw,
    )


def test_http_client_sends_signed_envelope():
    fake = FakeAIVA()
    envelope = _envelope()
    result = _http(fake).send_event(envelope)
    assert result.ok and result.status_code == 202
    request = fake.requests[0]
    assert str(request.url) == "https://aiva.test/api/integration/zalo-ai/v1/events"
    assert request.headers["Idempotency-Key"] == envelope["event_id"]
    assert request.headers["X-Event-Type"] == "consent.changed"
    assert request.headers["X-Tenant-Ref"] == "crs-a"
    assert request.headers["Authorization"] == "Bearer aiva-issued-token"
    assert json.loads(request.content) == envelope


@pytest.mark.parametrize(
    "status,ok,retryable",
    [
        (200, True, True),
        (409, True, True),
        (500, False, True),
        (429, False, True),
        (400, False, False),
        (401, False, False),
    ],
)
def test_http_client_status_mapping(status, ok, retryable):
    result = _http(FakeAIVA(status=status)).send_event(_envelope())
    assert result.ok is ok
    if not ok:
        assert result.retryable is retryable


def test_http_client_network_error_is_retryable_and_health():
    down = _http(FakeAIVA(raise_error=httpx.ConnectError("refused")))
    result = down.send_event(_envelope())
    assert not result.ok and result.retryable and "network" in result.detail
    assert down.health() is False
    assert _http(FakeAIVA()).health() is True


# --- Delivery worker ----------------------------------------------------------------


@pytest.mark.django_db
def test_deliver_due_sends_and_audits(sandbox, tenant_a):
    item = enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert delivery.deliver_due() == {"sent": 1, "failed": 0, "dead": 0}
    item.refresh_from_db()
    assert item.status == "sent" and item.attempts == 1 and item.sent_at
    assert sandbox.received[0]["event_id"] == str(item.event_id)
    assert IntegrationAuditLog.objects.filter(
        event_id=item.event_id, success=True, detail="delivered"
    ).exists()
    assert delivery.deliver_due() == {"sent": 0, "failed": 0, "dead": 0}  # nothing left


@pytest.mark.django_db
def test_retry_with_backoff_then_dead_letter(sandbox, tenant_a, settings):
    settings.AIVA_OUTBOX_MAX_ATTEMPTS = 3
    item = enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    failing = MockAIVAClient(fail_with=503)
    assert delivery.deliver_one(item.pk, client=failing) == "failed"
    item.refresh_from_db()
    assert item.attempts == 1 and item.next_attempt_at > timezone.now()
    assert delivery.deliver_one(item.pk, client=failing) is None  # not due yet
    for _ in range(2):
        IntegrationOutbox.objects.filter(pk=item.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        status = delivery.deliver_one(item.pk, client=failing)
    assert status == "dead"
    item.refresh_from_db()
    assert item.attempts == 3 and "mock failure" in item.last_error


@pytest.mark.django_db
def test_contract_error_is_dead_at_once(sandbox, tenant_a):
    item = enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert delivery.deliver_one(item.pk, client=MockAIVAClient(fail_with=400)) == "dead"


@pytest.mark.django_db
def test_missing_config_is_retried_not_crashing(sandbox, tenant_a, settings):
    settings.AIVA_CLIENT = "http"
    settings.AIVA_BASE_URL = ""
    item = enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert delivery.deliver_due()["failed"] == 1
    item.refresh_from_db()
    assert "AIVA_BASE_URL" in item.last_error


@pytest.mark.django_db
def test_mode_off_freezes_queue(sandbox, tenant_a, settings):
    item = enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    settings.AIVA_INTEGRATION_MODE = "off"
    assert delivery.deliver_due() == {"sent": 0, "failed": 0, "dead": 0}
    assert delivery.deliver_one(item.pk) is None
    item.refresh_from_db()
    assert item.status == "pending" and sandbox.received == []


@pytest.mark.django_db
def test_real_row_is_never_sent_in_sandbox(sandbox, tenant_a):
    envelope = build_envelope(
        event_type="consent.changed", tenant_ref="crs-a", data_origin="real", payload=PAYLOAD
    )
    item = IntegrationOutbox.objects.create(
        tenant=tenant_a,
        event_id=envelope["event_id"],
        event_type="consent.changed",
        payload=envelope,
        data_origin="real",
        idempotency_key="x",
    )
    # Phase 6: frozen (not dead-lettered), so a live -> sandbox rollback loses nothing
    assert delivery.deliver_due() == {"sent": 0, "failed": 0, "dead": 0}
    assert delivery.deliver_one(item.pk) is None
    item.refresh_from_db()
    assert item.status == "pending" and item.attempts == 0 and sandbox.received == []


def test_backoff_is_exponential_and_capped(settings):
    settings.AIVA_OUTBOX_BACKOFF_BASE_SECONDS = 30
    settings.AIVA_OUTBOX_BACKOFF_MAX_SECONDS = 3600
    assert [delivery.backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert delivery.backoff_seconds(20) == 3600


@pytest.mark.django_db
def test_idempotency_key_dedupes(sandbox, tenant_a):
    first = enqueue_event(
        tenant_a, "consent.changed", PAYLOAD, data_origin="demo", idempotency_key="k1"
    )
    again = enqueue_event(
        tenant_a, "consent.changed", PAYLOAD, data_origin="demo", idempotency_key="k1"
    )
    assert first.pk == again.pk and IntegrationOutbox.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_delivery_scheduled_after_commit(sandbox, tenant_a):
    # Celery runs eagerly in tests: committing the outbox row delivers it.
    enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert IntegrationOutbox.objects.get().status == "sent"
    assert len(sandbox.received) == 1


@pytest.mark.django_db
def test_schedule_survives_broker_down(
    sandbox, tenant_a, monkeypatch, django_capture_on_commit_callbacks
):
    from apps.integrations.aiva import tasks

    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(tasks.deliver_outbox_item, "delay", boom)
    with django_capture_on_commit_callbacks(execute=True):
        enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert IntegrationOutbox.objects.get().status == "pending"  # beat picks it up later


@pytest.mark.django_db
def test_beat_task(sandbox, tenant_a):
    from apps.integrations.aiva.tasks import deliver_due_outbox

    enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert deliver_due_outbox.apply().get()["sent"] == 1


# --- Monitoring API (tenant users) --------------------------------------------------


@pytest.mark.django_db
def test_status_outbox_and_retry_api(sandbox, tenant_a, tenant_b, client_a, client_b):
    item = enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    delivery.deliver_one(item.pk, client=MockAIVAClient(fail_with=400))
    status = client_a.get("/api/integrations/aiva/status/").json()
    assert status["mode"] == "sandbox" and status["allowed_data_origins"] == ["demo"]
    assert status["outbox"]["dead"] == 1 and status["tenant_ref"] == "crs-a"
    listed = client_a.get("/api/integrations/aiva/outbox/?status=dead").json()["results"]
    assert [row["id"] for row in listed] == [item.pk]
    assert client_a.get("/api/integrations/aiva/exchanges/").json()["count"] >= 2

    # tenant B sees nothing of tenant A
    assert client_b.get("/api/integrations/aiva/outbox/").json()["results"] == []
    assert client_b.get("/api/integrations/aiva/exchanges/").json()["results"] == []
    assert client_b.post(f"/api/integrations/aiva/outbox/{item.pk}/retry/").status_code == 404
    # viewers may look, not re-queue
    viewer = auth_client(UserFactory(tenant=tenant_a, role="viewer"))
    assert viewer.post(f"/api/integrations/aiva/outbox/{item.pk}/retry/").status_code == 403

    response = client_a.post(f"/api/integrations/aiva/outbox/{item.pk}/retry/")
    assert response.status_code == 200 and response.json()["status"] == "pending"
    assert delivery.deliver_due()["sent"] == 1
