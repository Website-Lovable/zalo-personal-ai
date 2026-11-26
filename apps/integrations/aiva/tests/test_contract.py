"""Contract tests (Kickoff 2.4): what Zalo AI sends and accepts matches docs/integration/*.json.

If one of these fails, the published contract and the code disagree: fix one of them and
tell the AIVA team before shipping.
"""

import hashlib
import hmac
from datetime import timedelta

import pytest
from django.utils import timezone
from jsonschema import Draft202012Validator

from apps.ai_engine.models import AIReplyLog
from apps.integrations.aiva import delivery, events
from apps.integrations.aiva.inbound import load_inbound_schema
from apps.integrations.aiva.models import IntegrationOutbox
from apps.integrations.aiva.schema import EVENT_TYPES, load_schema, validate_envelope
from apps.integrations.aiva.signing import request_message, sign_request_headers
from apps.memory.models import LongTermMemory, ShortTermMemory
from apps.permissions.models import ApprovalQueue
from tests.factories import MessageFactory


def test_published_schemas_are_valid_json_schema():
    Draft202012Validator.check_schema(load_schema())
    Draft202012Validator.check_schema(load_inbound_schema())
    assert set(load_inbound_schema()["$defs"]) >= {"contacts_upsert", "outreach_draft"}


def test_request_signature_known_vector():
    secret, ts = "s3cret", 1_800_000_000
    msg = b"1800000000.GET./api/integration/v1/health.crs-a."
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    assert sign_request_headers(secret, "get", "/api/integration/v1/health", "crs-a", now=ts) == {
        "X-Signature": f"sha256={expected}",
        "X-Timestamp": str(ts),
    }
    assert request_message("POST", "/p", "t", b"{}") == b"POST./p.t.{}"


@pytest.mark.django_db
def test_every_producer_emits_a_valid_envelope(sandbox, demo_conversation, client_a):
    """Drive each producer once and deliver through MockAIVAClient (validates the schema)."""
    contact = demo_conversation.contact
    tenant = demo_conversation.tenant
    common = {"tenant": tenant, "data_origin": "demo"}
    contact.has_messaged_first = True
    contact.save()

    events.lead_inbound(contact)
    ShortTermMemory.objects.create(
        **common,
        conversation=demo_conversation,
        context_summary="Tóm tắt",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    AIReplyLog.objects.create(
        **common,
        conversation=demo_conversation,
        inbound_message=MessageFactory(conversation=demo_conversation, data_origin="demo"),
        outcome="need_approval",
        intents=["price"],
        confidence=0.7,
    )
    ApprovalQueue.objects.create(
        **common,
        kind="escalation",
        contact=contact,
        conversation=demo_conversation,
        proposed_response="",
        reasons=["handoff_requested"],
        expires_at=timezone.now() + timedelta(hours=1),
    )
    LongTermMemory.objects.create(
        **common, contact=contact, content="Cần hóa đơn VAT", importance=9
    )
    client_a.patch(f"/api/contacts/{contact.pk}/", {"consent_status": "withdrawn"}, format="json")

    assert set(IntegrationOutbox.objects.values_list("event_type", flat=True)) == set(EVENT_TYPES)
    counts = delivery.deliver_due()
    assert counts == {"sent": len(EVENT_TYPES), "failed": 0, "dead": 0}
    for envelope in sandbox.received:
        validate_envelope(envelope)
        assert envelope["tenant_ref"] == "crs-a" and envelope["data_origin"] == "demo"
        assert envelope["payload"]["contact_ref"] == str(contact.pk)


@pytest.mark.django_db
def test_sandbox_run_command(settings):
    from io import StringIO

    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError):
        call_command("aiva_sandbox_run")  # mode off: refused
    settings.AIVA_INTEGRATION_MODE = "sandbox"
    out = StringIO()
    call_command("aiva_sandbox_run", stdout=out)
    text = out.getvalue()
    assert "ĐẠT" in text and "handoff.requested" in text and "data_origin_not_allowed" in text
    assert not IntegrationOutbox.objects.filter(data_origin="real").exists()
