import copy
import uuid

import pytest
from django.test import override_settings

from apps.integrations.aiva import config
from apps.integrations.aiva.client import DisabledAIVAClient, MockAIVAClient, get_aiva_client
from apps.integrations.aiva.models import IntegrationAuditLog, IntegrationOutbox
from apps.integrations.aiva.outbox import enqueue_event
from apps.integrations.aiva.schema import (
    EnvelopeError,
    build_envelope,
    load_schema,
    validate_envelope,
)
from apps.integrations.aiva.signing import (
    SignatureError,
    compute_signature,
    sign_headers,
    verify_signature,
)
from common.exceptions import FeatureDisabledError, NotImplementedInPhase
from tests.factories import ChannelFactory, ContactFactory

SECRET = "unit-test-secret"
BODY = b'{"hello":"world"}'
NOW = 1_800_000_000


# --- HMAC signing ------------------------------------------------------------------


def test_sign_and_verify_roundtrip():
    headers = sign_headers(SECRET, BODY, now=NOW)
    assert headers["X-Signature"].startswith("sha256=") and headers["X-Timestamp"] == str(NOW)
    verify_signature(SECRET, BODY, headers["X-Signature"], headers["X-Timestamp"], now=NOW + 10)


def test_signature_known_vector():
    # sha256 HMAC of "1800000000.{...}" with the test secret; guards against format drift.
    import hashlib
    import hmac

    expected = hmac.new(SECRET.encode(), b"1800000000." + BODY, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, NOW, BODY) == f"sha256={expected}"


@pytest.mark.parametrize(
    "signature,timestamp,now,body",
    [
        (None, str(NOW), NOW, BODY),  # missing header
        ("md5=abc", str(NOW), NOW, BODY),  # wrong scheme
        ("sha256=abc", "yesterday", NOW, BODY),  # bad timestamp
        ("sha256=abc", str(NOW), NOW, BODY),  # wrong signature
        ("VALID", str(NOW), NOW + 301, BODY),  # replay: too old
        ("VALID", str(NOW), NOW - 301, BODY),  # clock skew: too new
        ("VALID", str(NOW), NOW, b'{"hello":"tampered"}'),  # body changed
    ],
)
def test_verify_rejects(signature, timestamp, now, body):
    if signature == "VALID":
        signature = compute_signature(SECRET, NOW, BODY)
    with pytest.raises(SignatureError):
        verify_signature(SECRET, body, signature, timestamp, now=now)


def test_empty_secret_is_refused():
    with pytest.raises(SignatureError):
        compute_signature("", NOW, BODY)


# --- Schema v1 ---------------------------------------------------------------------


def _envelope(**payload_overrides):
    payload = {"contact_ref": "1", "consent_status": "granted", **payload_overrides}
    return build_envelope(
        event_type="consent.changed", tenant_ref="crs-a", data_origin="demo", payload=payload
    )


def test_schema_file_lists_all_events():
    schema = load_schema()
    assert set(schema["properties"]["event_type"]["enum"]) == {
        "lead.inbound",
        "conversation.summary",
        "opportunity.suggested",
        "handoff.requested",
        "consent.changed",
        "profile.update_suggested",
    }


def test_build_valid_envelope():
    envelope = _envelope()
    assert envelope["schema_version"] == "1.0"
    uuid.UUID(envelope["event_id"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.pop("tenant_ref"),
        lambda e: e.update(data_origin="fake"),
        lambda e: e.update(event_type="chat.full_history"),
        lambda e: e.update(schema_version="2.0"),
        lambda e: e.update(extra="x"),
        lambda e: e["payload"].update(consent_status="maybe"),
        lambda e: e["payload"].pop("contact_ref"),
    ],
)
def test_invalid_envelopes(mutate):
    envelope = copy.deepcopy(_envelope())
    mutate(envelope)
    with pytest.raises(EnvelopeError):
        validate_envelope(envelope)


@pytest.mark.parametrize(
    "event_type,payload",
    [
        (
            "lead.inbound",
            {
                "contact_ref": "1",
                "display_name": "A",
                "channel_type": "zalo_oa",
                "consent_status": "granted",
            },
        ),
        ("conversation.summary", {"contact_ref": "1", "conversation_ref": "9", "summary": "..."}),
        ("opportunity.suggested", {"contact_ref": "1", "signals": ["budget"], "confidence": 0.6}),
        ("handoff.requested", {"contact_ref": "1", "reason": "ai_uncertain"}),
        ("profile.update_suggested", {"contact_ref": "1", "suggested_changes": {"phone": "x"}}),
    ],
)
def test_each_event_type_validates(event_type, payload):
    build_envelope(event_type=event_type, tenant_ref="t", data_origin="demo", payload=payload)


def test_lead_inbound_requires_consent():
    with pytest.raises(EnvelopeError):
        build_envelope(
            event_type="lead.inbound",
            tenant_ref="t",
            data_origin="demo",
            payload={
                "contact_ref": "1",
                "display_name": "A",
                "channel_type": "zalo_oa",
                "consent_status": "unknown",
            },
        )


# --- Clients -----------------------------------------------------------------------


def test_mode_off_uses_disabled_client():
    assert config.get_mode() == "off" and not config.is_enabled()
    client = get_aiva_client()
    assert isinstance(client, DisabledAIVAClient) and client.health() is False
    with pytest.raises(FeatureDisabledError):
        client.send_event(_envelope())


@override_settings(AIVA_INTEGRATION_MODE="sandbox")
def test_http_client_not_available_before_phase_3_5():
    with pytest.raises(NotImplementedInPhase):
        get_aiva_client()


def test_mock_client_contract():
    client = MockAIVAClient()
    assert client.health()
    assert client.send_event(_envelope()).status_code == 202
    assert len(client.received) == 1
    assert not MockAIVAClient(fail_with=503).send_event(_envelope()).ok
    with pytest.raises(EnvelopeError):
        client.send_event({"event_type": "x"})


# --- Outbox guards -----------------------------------------------------------------

PAYLOAD = {"contact_ref": "1", "consent_status": "granted"}


@pytest.mark.django_db
def test_outbox_off_enqueues_nothing(tenant_a):
    assert enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo") is None
    assert IntegrationOutbox.objects.count() == 0
    assert IntegrationAuditLog.objects.count() == 0


@pytest.mark.django_db
@override_settings(AIVA_INTEGRATION_MODE="sandbox")
def test_outbox_sandbox_accepts_only_demo(tenant_a):
    item = enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo")
    assert item is not None and item.payload["tenant_ref"] == "crs-a"
    assert enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="real") is None
    assert IntegrationOutbox.objects.count() == 1
    blocked = IntegrationAuditLog.objects.get(success=False)
    assert "not allowed" in blocked.detail
    assert IntegrationAuditLog.objects.filter(success=True, event_id=item.event_id).exists()


@pytest.mark.django_db
@override_settings(AIVA_INTEGRATION_MODE="live")
def test_outbox_live_accepts_only_real(tenant_a):
    assert config.allowed_data_origins() == {"real"}
    assert enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo") is None
    assert enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="real") is not None


@pytest.mark.django_db
@override_settings(AIVA_INTEGRATION_MODE="sandbox")
def test_outbox_blocks_personal_contact_without_consent_and_missing_tenant_ref(tenant_a):
    personal = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    contact = ContactFactory(channel=personal)
    assert (
        enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo", contact=contact)
        is None
    )
    tenant_a.external_ref = ""
    assert enqueue_event(tenant_a, "consent.changed", PAYLOAD, data_origin="demo") is None
    assert IntegrationOutbox.objects.count() == 0
    assert IntegrationAuditLog.objects.filter(success=False).count() == 2


def test_invalid_mode_rejected_at_startup(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    import config.settings.base as base

    monkeypatch.setenv("AIVA_INTEGRATION_MODE", "turbo")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base)
    monkeypatch.setenv("AIVA_INTEGRATION_MODE", "off")
    importlib.reload(base)
