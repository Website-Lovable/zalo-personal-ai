"""Phase 6: live (real-data) gate, preflight review, audit trail and rollback levels."""

import importlib
import io

import httpx
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.integrations.aiva import config, delivery, events, live
from apps.integrations.aiva.client import MockAIVAClient
from apps.integrations.aiva.models import (
    AIVALiveEnrollment,
    IntegrationAuditLog,
    IntegrationOutbox,
)
from apps.integrations.aiva.outbox import enqueue_event
from apps.integrations.aiva.preflight import run_preflight
from common.models import AuditLog
from conftest import auth_client
from tests.factories import ChannelFactory, ContactFactory, UserFactory

pytestmark = pytest.mark.django_db

WEBHOOK = "w" * 40
INBOUND = "i" * 40
TOKEN = "t" * 40
LIVE = {
    "AIVA_INTEGRATION_MODE": "live",
    "AIVA_LIVE_APPROVED": True,
    "AIVA_LIVE_APPROVAL_REF": "QD-06/2026 GĐ duyệt",
    "AIVA_CLIENT": "http",
    "AIVA_BASE_URL": "https://aiva.example",
    "AIVA_WEBHOOK_SECRET": WEBHOOK,
    "AIVA_INBOUND_SECRET": INBOUND,
    "AIVA_SERVICE_TOKEN": TOKEN,
    "DEBUG": False,
}
PAYLOAD = {"contact_ref": "1", "consent_status": "granted"}


@pytest.fixture
def live_mode():
    with override_settings(**LIVE):
        yield


@pytest.fixture
def real_contact(tenant_a):
    channel = ChannelFactory(tenant=tenant_a, data_origin="real", status="active")
    contact = ContactFactory(
        channel=channel, data_origin="real", crs_customer_id="CRS-9", owner_staff_ref="s-1"
    )
    contact.set_consent("granted", "crs")
    contact.save()
    return contact


@pytest.fixture
def acknowledged(tenant_a, owner_a):
    return live.acknowledge(tenant_a, owner_a)


@pytest.fixture
def active(live_mode, tenant_a, acknowledged):
    return live.activate(tenant_a, operator="Ops Sơn", approval_ref="QD-06")


def _real_row(tenant, key="k"):
    return enqueue_event(
        tenant, "consent.changed", PAYLOAD, data_origin="real", idempotency_key=key
    )


# --- the gate ----------------------------------------------------------------------------


def test_non_live_modes_are_not_affected(tenant_a):
    assert live.can_enqueue(tenant_a) == (True, "")
    assert live.can_exchange(tenant_a) == (True, "")


@override_settings(AIVA_INTEGRATION_MODE="live", AIVA_LIVE_APPROVED=False)
def test_live_without_approval_is_closed(tenant_a):
    assert live.can_enqueue(tenant_a)[0] is False
    assert live.can_exchange(tenant_a)[0] is False


def test_live_needs_an_active_enrollment(live_mode, tenant_a, owner_a):
    assert live.can_enqueue(tenant_a) == (False, "tenant not activated for live")
    assert _real_row(tenant_a) is None
    blocked = IntegrationAuditLog.objects.get(success=False)
    assert "not activated" in blocked.detail
    live.acknowledge(tenant_a, owner_a)
    assert live.can_exchange(tenant_a) == (False, "tenant live state: acknowledged")
    assert _real_row(tenant_a) is None  # acknowledgement alone opens nothing


def test_active_tenant_exchanges_and_other_tenant_does_not(active, tenant_a, tenant_b):
    assert live.can_exchange(tenant_a) == (True, "")
    assert live.can_exchange(tenant_b)[0] is False
    item = _real_row(tenant_a)
    assert item is not None
    assert _real_row(tenant_b, key="b") is None
    client = MockAIVAClient()
    assert delivery.deliver_due(client=client)["sent"] == 1
    assert [e["event_id"] for e in client.received] == [str(item.event_id)]


def test_live_never_sends_demo(active, tenant_a):
    assert enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo") is None


def test_undocumented_consent_is_not_shared_in_live(active, real_contact):
    real_contact.consent_source = ""
    real_contact.save()
    assert not real_contact.has_documented_consent
    assert events.lead_inbound(real_contact) is None
    item = enqueue_event(
        real_contact.tenant, "lead.inbound", PAYLOAD, data_origin="real", contact=real_contact
    )
    assert item is None
    assert IntegrationAuditLog.objects.filter(detail__contains="consent not documented").exists()
    # consent.changed is the one event allowed about such a contact (CRS must hear it)
    assert (
        enqueue_event(
            real_contact.tenant,
            "consent.changed",
            PAYLOAD,
            data_origin="real",
            contact=real_contact,
        )
        is not None
    )


def test_documented_consent_is_shared_in_live(active, real_contact):
    assert events.lead_inbound(real_contact) is not None


# --- state changes and audit -------------------------------------------------------------


def test_only_owner_acknowledges(tenant_a, admin_a):
    with pytest.raises(live.LiveStateError):
        live.acknowledge(tenant_a, admin_a)


def test_acknowledge_is_audited_with_text_version(acknowledged, tenant_a, owner_a):
    assert acknowledged.status == "acknowledged"
    assert acknowledged.acknowledgement_text == live.ACKNOWLEDGEMENT_TEXT
    entry = AuditLog.objects.get(action="aiva.live_acknowledged")
    assert entry.user == owner_a and entry.tenant == tenant_a
    assert entry.changes["version"] == live.ACKNOWLEDGEMENT_VERSION


def test_activate_requires_preflight_and_acknowledgement(tenant_a, owner_a, live_mode):
    with pytest.raises(live.LiveStateError, match="owner_acknowledgement"):
        live.activate(tenant_a, operator="ops", approval_ref="QD")
    with pytest.raises(live.LiveStateError, match="người thực hiện"):
        live.activate(tenant_a, operator="", approval_ref="")
    live.acknowledge(tenant_a, owner_a)
    with override_settings(AIVA_BASE_URL="http://insecure"):
        with pytest.raises(live.LiveStateError, match="base_url"):
            live.activate(tenant_a, operator="ops", approval_ref="QD")
    enrollment = live.activate(tenant_a, operator="ops", approval_ref="QD")
    assert enrollment.status == "active" and enrollment.approval_ref == "QD"
    entry = AuditLog.objects.get(action="aiva.live_activated")
    assert entry.changes["approval_ref"] == "QD" and entry.changes["preflight"]["passed"]
    with pytest.raises(live.LiveStateError):  # cannot activate twice
        live.activate(tenant_a, operator="ops", approval_ref="QD")
    with pytest.raises(live.LiveStateError):  # nor re-acknowledge while active
        live.acknowledge(tenant_a, owner_a)


def test_pause_freezes_and_resume_delivers(active, tenant_a):
    item = _real_row(tenant_a)
    with pytest.raises(live.LiveStateError):
        live.pause(tenant_a, by="ops", reason="")
    live.pause(tenant_a, by="ops", reason="CRS báo lỗi")
    assert live.can_exchange(tenant_a)[0] is False
    assert live.can_enqueue(tenant_a)[0] is True  # still queued while paused
    client = MockAIVAClient()
    assert delivery.deliver_due(client=client) == {"sent": 0, "failed": 0, "dead": 0}
    assert delivery.deliver_one(item.pk, client=client) is None
    with pytest.raises(live.LiveStateError):
        live.pause(tenant_a, by="ops", reason="again")
    live.resume(tenant_a, operator="ops")
    assert delivery.deliver_due(client=client)["sent"] == 1
    with pytest.raises(live.LiveStateError):
        live.resume(tenant_a, operator="ops")
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"aiva.live_paused", "aiva.live_resumed"} <= actions


def test_resume_reruns_preflight(active, tenant_a):
    live.pause(tenant_a, by="ops", reason="x")
    with override_settings(AIVA_WEBHOOK_SECRET="short"):
        with pytest.raises(live.LiveStateError, match="secrets_weak"):
            live.resume(tenant_a, operator="ops")


def test_global_pause_stops_every_tenant(active, tenant_a):
    _real_row(tenant_a)
    with pytest.raises(live.LiveStateError):
        live.set_global_pause(True, by="ops", reason="")
    live.set_global_pause(True, by="ops", reason="sự cố")
    assert live.can_exchange(tenant_a) == (False, "live exchange paused platform-wide")
    assert delivery.deliver_due(client=MockAIVAClient())["sent"] == 0
    live.set_global_pause(False, by="ops", reason="")
    assert delivery.deliver_due(client=MockAIVAClient())["sent"] == 1
    assert AuditLog.objects.filter(action="aiva.live_global_paused").exists()
    assert AuditLog.objects.filter(action="aiva.live_global_resumed").exists()


def test_revoke_drops_unsent_real_events(active, tenant_a):
    item = _real_row(tenant_a)
    live.revoke(tenant_a, by="owner", reason="không đồng ý nữa")
    item.refresh_from_db()
    assert item.status == "dead" and "discarded" in item.last_error
    assert live.can_enqueue(tenant_a)[0] is False
    entry = AuditLog.objects.get(action="aiva.live_revoked")
    assert entry.changes["dropped_events"] == 1 and entry.changes["previous"] == "active"
    with pytest.raises(live.LiveStateError):
        live.revoke(tenant_a, by="owner", reason="")


def test_revoked_tenant_can_acknowledge_again(active, tenant_a, owner_a):
    live.revoke(tenant_a, by="owner", reason="")
    assert live.acknowledge(tenant_a, owner_a).status == "acknowledged"


def test_rollback_to_sandbox_keeps_real_events_frozen(active, tenant_a):
    item = _real_row(tenant_a)
    with override_settings(AIVA_INTEGRATION_MODE="sandbox", AIVA_CLIENT="mock"):
        assert delivery.deliver_due() == {"sent": 0, "failed": 0, "dead": 0}
    item.refresh_from_db()
    assert item.status == "pending"
    assert delivery.deliver_due(client=MockAIVAClient())["sent"] == 1  # back to live


def test_mode_change_is_audited():
    assert live.record_mode() == ""  # first sight
    assert live.record_mode() is None  # unchanged
    with override_settings(**LIVE):
        assert live.record_mode() == "off"
        delivery.deliver_due(client=MockAIVAClient())  # the beat task also records
    entries = AuditLog.objects.filter(action="aiva.mode_changed").order_by("id")
    assert [e.changes["to"] for e in entries] == ["off", "live"]
    assert entries.last().changes["approval_ref"] == LIVE["AIVA_LIVE_APPROVAL_REF"]


def test_live_with_mock_client_refused_at_startup(monkeypatch):
    import config.settings.base as base

    monkeypatch.setenv("AIVA_INTEGRATION_MODE", "live")
    monkeypatch.setenv("AIVA_LIVE_APPROVED", "true")
    monkeypatch.setenv("AIVA_CLIENT", "mock")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base)
    monkeypatch.setenv("AIVA_INTEGRATION_MODE", "off")
    monkeypatch.setenv("AIVA_LIVE_APPROVED", "false")
    monkeypatch.setenv("AIVA_CLIENT", "http")
    importlib.reload(base)


# --- inbound (AIVA -> Zalo AI) -----------------------------------------------------------


def _health(aiva, tenant_ref="crs-a"):
    return aiva("GET", "health", tenant_ref=tenant_ref, token=TOKEN, secret=INBOUND)


def test_inbound_closed_until_tenant_active(aiva, live_mode, tenant_a, acknowledged):
    response = _health(aiva)
    assert response.status_code == 503
    assert IntegrationAuditLog.objects.filter(status_code=503, tenant=tenant_a).exists()
    live.activate(tenant_a, operator="ops", approval_ref="QD")
    assert _health(aiva).json()["mode"] == "live"
    live.pause(tenant_a, by="ops", reason="x")
    assert _health(aiva).status_code == 503


# --- preflight review --------------------------------------------------------------------


def _codes(report, level):
    return {c.code for c in report.checks if c.level == level}


def test_preflight_off_mode_fails_server_checks():
    report = run_preflight()
    assert not report.passed
    assert {"mode", "director_approval", "base_url", "secrets_weak"} <= _codes(report, "fail")


def test_preflight_passes_for_a_ready_tenant(live_mode, tenant_a, acknowledged, real_contact):
    UserFactory(tenant=tenant_a, role="viewer", staff_ref="s-1")
    report = run_preflight(tenant=tenant_a)
    assert report.passed, report.as_dict()
    assert "staff_unmapped" not in _codes(report, "warn")
    assert report.as_dict()["checks"]


def test_preflight_consent_and_permission_review(live_mode, tenant_a, acknowledged, real_contact):
    incomplete = ContactFactory(channel=real_contact.channel, data_origin="real")
    incomplete.consent_status = "granted"  # no source, no time
    incomplete.save()
    inbound = ContactFactory(channel=real_contact.channel, data_origin="real")
    inbound.set_consent("granted", "zalo_inbound")
    inbound.save()
    personal = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal", data_origin="real")
    friend = ContactFactory(channel=personal, data_origin="real")
    friend.set_consent("granted", "manual")
    friend.save()
    report = run_preflight(tenant=tenant_a)
    assert "consent_incomplete" in _codes(report, "fail")
    assert {"consent_inbound_only", "consent_personal_channel", "staff_unmapped"} <= _codes(
        report, "warn"
    )


def test_preflight_tenant_problems(live_mode, tenant_a):
    tenant_a.external_ref = ""
    tenant_a.is_active = False
    tenant_a.save()
    report = run_preflight(tenant=tenant_a, server=False)
    assert {"tenant_ref", "tenant_active", "owner_acknowledgement", "owner_present"} <= _codes(
        report, "fail"
    )
    assert "audit_trail" in _codes(report, "warn")


def test_preflight_server_details(live_mode, tenant_a):
    with override_settings(AIVA_INBOUND_SECRET=WEBHOOK, DEBUG=True):
        report = run_preflight()
    assert {"secrets_reused", "debug"} <= _codes(report, "fail")
    live.set_global_pause(True, by="ops", reason="x")
    assert "global_switch" in _codes(run_preflight(), "warn")


def test_preflight_warns_about_frozen_demo_events(tenant_a, live_mode):
    with override_settings(AIVA_INTEGRATION_MODE="sandbox", AIVA_CLIENT="mock"):
        enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert "demo_outbox_pending" in _codes(run_preflight(tenant=tenant_a), "warn")


def test_preflight_health_check(live_mode, monkeypatch):
    from apps.integrations.aiva import client as client_module

    def transport(ok):
        return httpx.MockTransport(lambda request: httpx.Response(200 if ok else 500))

    real_init = client_module.HTTPAIVAClient.__init__

    for ok, level in ((True, "ok"), (False, "fail")):

        def init(self, *a, _ok=ok, **kw):
            real_init(self, *a, transport=transport(_ok), **kw)

        monkeypatch.setattr(client_module.HTTPAIVAClient, "__init__", init)
        assert "aiva_health" in _codes(run_preflight(check_health=True), level)
    with override_settings(AIVA_BASE_URL=""):  # client cannot even be built
        assert "aiva_health" in _codes(run_preflight(check_health=True), "fail")


# --- tenant API ----------------------------------------------------------------------------


def test_live_api_roles_and_flow(live_mode, tenant_a, owner_a, admin_a, viewer_a):
    owner, admin, viewer = auth_client(owner_a), auth_client(admin_a), auth_client(viewer_a)
    assert viewer.get("/api/integrations/aiva/live/").status_code == 403
    body = admin.get("/api/integrations/aiva/live/").json()
    assert body["enrollment"] is None and body["review"]["passed"] is False
    assert body["acknowledgement_text"] == live.ACKNOWLEDGEMENT_TEXT

    url = "/api/integrations/aiva/live/{}/"
    assert admin.post(url.format("acknowledge"), {"confirm": True}).status_code == 403
    assert owner.post(url.format("acknowledge"), {}).status_code == 400
    assert owner.post(url.format("bogus"), {}).status_code == 404
    response = owner.post(url.format("acknowledge"), {"confirm": True}, format="json")
    assert response.status_code == 200
    assert response.json()["enrollment"]["status"] == "acknowledged"
    assert response.json()["enrollment"]["acknowledged_by"] == owner_a.username
    assert admin.post(url.format("pause"), {"reason": "x"}).status_code == 409  # not active

    live.activate(tenant_a, operator="ops", approval_ref="QD")
    status = owner.get("/api/integrations/aiva/status/").json()
    assert status["live_exchange"] is True
    response = admin.post(url.format("pause"), {"reason": "khách phàn nàn"}, format="json")
    assert response.json()["enrollment"]["status"] == "paused"
    assert AuditLog.objects.get(action="aiva.live_paused").user == admin_a
    assert admin.post(url.format("revoke"), {}).status_code == 403
    assert owner.post(url.format("revoke"), {"reason": "dừng"}).json()["enrollment"]["status"] == (
        "revoked"
    )


def test_live_api_is_tenant_scoped(live_mode, tenant_a, owner_a, client_b):
    live.acknowledge(tenant_a, owner_a)
    assert client_b.get("/api/integrations/aiva/live/").json()["enrollment"] is None
    client_b.post("/api/integrations/aiva/live/acknowledge/", {"confirm": True}, format="json")
    assert AIVALiveEnrollment.objects.count() == 2
    assert AIVALiveEnrollment.objects.get(tenant=tenant_a).acknowledged_by == owner_a


def test_status_endpoint_live_flag_when_off(client_a):
    assert client_a.get("/api/integrations/aiva/status/").json()["live_exchange"] is False


# --- operator command ----------------------------------------------------------------------


def _run(*args):
    out = io.StringIO()
    call_command("aiva_live", *args, stdout=out)
    return out.getvalue()


def test_command_flow(live_mode, tenant_a, acknowledged):
    slug = tenant_a.slug
    with pytest.raises(CommandError):
        _run("activate", "--tenant", slug)  # --by missing
    with pytest.raises(CommandError):
        _run("activate", "--by", "ops")  # --tenant missing
    with pytest.raises(CommandError):
        _run("activate", "--tenant", "nope", "--by", "ops")
    with pytest.raises(CommandError):
        _run("activate", "--tenant", slug, "--by", "ops")  # approval ref missing
    assert "Đang kết nối" in _run(
        "activate", "--tenant", slug, "--by", "ops", "--approval-ref", "QD"
    )
    _real_row(tenant_a)
    out = _run("status")
    assert f"{slug}: active" in out and "chờ gửi: 1" in out
    assert "Tạm dừng" in _run("pause", "--tenant", slug, "--by", "ops", "--reason", "x")
    assert "Đang kết nối" in _run("resume", "--tenant", slug, "--by", "ops")
    assert "ĐANG DỪNG" in _run("pause-all", "--by", "ops", "--reason", "sự cố")
    assert "đang mở" in _run("resume-all", "--by", "ops")
    assert "thu hồi" in _run("revoke", "--tenant", slug, "--by", "ops", "--reason", "x")


def test_command_preflight_output_and_exit_code(live_mode, tenant_a, acknowledged):
    out = _run("preflight", "--tenant", tenant_a.slug)
    assert "Kết quả preflight: ĐẠT" in out
    assert '"passed": true' in _run("preflight", "--tenant", tenant_a.slug, "--json")
    with override_settings(AIVA_INTEGRATION_MODE="sandbox", AIVA_CLIENT="mock"):
        with pytest.raises(SystemExit) as exc:
            _run("preflight")
    assert exc.value.code == 1


def test_mode_helpers_still_hold():
    assert config.get_mode() == "off"
    assert IntegrationOutbox.objects.count() == 0
