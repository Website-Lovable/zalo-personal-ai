"""Phase 3.5: which app events become AIVA outbox rows (and which never do)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ai_engine.models import AIReplyLog
from apps.channels.providers.base import ChannelEvent, InboundMessage
from apps.conversations.inbound import handle_channel_event
from apps.integrations.aiva import events
from apps.integrations.aiva.models import IntegrationOutbox
from apps.memory.models import LongTermMemory, ShortTermMemory
from apps.permissions.models import ApprovalQueue
from tests.factories import ContactFactory, MessageFactory

pytestmark = pytest.mark.django_db


def _types(**filters):
    return list(IntegrationOutbox.objects.filter(**filters).values_list("event_type", flat=True))


def _event(user_id, text="Chào shop", message_id="m1"):
    return ChannelEvent(
        kind="message",
        event_name="user_send_text",
        dedupe_key=message_id,
        user_id=user_id,
        message=InboundMessage(
            message_id=message_id, sender_id=user_id, text=text, sender_name="Khách"
        ),
    )


@pytest.fixture(autouse=True)
def _no_ai(settings):
    settings.AI_ENGINE_ENABLED = False  # these tests look at the integration only


def test_lead_inbound_once_for_consented_contact(sandbox, demo_contact):
    handle_channel_event(demo_contact.channel, _event(demo_contact.zalo_user_id))
    handle_channel_event(demo_contact.channel, _event(demo_contact.zalo_user_id, message_id="m2"))
    rows = IntegrationOutbox.objects.filter(event_type="lead.inbound")
    assert rows.count() == 1
    payload = rows.get().payload["payload"]
    assert payload == {
        "contact_ref": str(demo_contact.pk),
        "crs_customer_id": "CRS-1",
        "display_name": demo_contact.display_name,
        "channel_type": "zalo_oa",
        "consent_status": "granted",
        "segment": "b2b",
    }
    assert "Chào shop" not in str(rows.get().payload)  # never the message itself


def test_no_lead_without_consent_or_when_off(sandbox, demo_channel, settings):
    handle_channel_event(demo_channel, _event("stranger"))
    assert _types() == []
    settings.AIVA_INTEGRATION_MODE = "off"
    contact = ContactFactory(channel=demo_channel, data_origin="demo")
    contact.set_consent("granted", "crs")
    contact.save()
    assert events.lead_inbound(contact) is None
    assert _types() == []


def test_consent_granted_later_sends_consent_and_lead(sandbox, demo_channel, client_a):
    contact = ContactFactory(channel=demo_channel, data_origin="demo", has_messaged_first=True)
    response = client_a.patch(
        f"/api/contacts/{contact.pk}/",
        {"consent_status": "granted", "consent_source": "form"},
        format="json",
    )
    assert response.status_code == 200
    assert sorted(_types()) == ["consent.changed", "lead.inbound"]


def test_summary_throttled_per_conversation(sandbox, demo_conversation):
    for text in ("Khách hỏi lịch khai giảng", "Khách hỏi học phí"):
        ShortTermMemory.objects.create(
            tenant=demo_conversation.tenant,
            conversation=demo_conversation,
            context_summary=text,
            expires_at=timezone.now() + timedelta(hours=1),
            data_origin="demo",
        )
    rows = IntegrationOutbox.objects.filter(event_type="conversation.summary")
    assert rows.count() == 1
    payload = rows.get().payload["payload"]
    assert payload["summary"] == "Khách hỏi lịch khai giảng"
    assert payload["conversation_ref"] == str(demo_conversation.pk)


def _run(conversation, intents, confidence=0.8):
    message = MessageFactory(conversation=conversation, data_origin="demo")
    return AIReplyLog.objects.create(
        tenant=conversation.tenant,
        conversation=conversation,
        inbound_message=message,
        outcome="need_approval",
        intents=intents,
        confidence=confidence,
        data_origin="demo",
    )


def test_opportunity_from_buying_intents_once(sandbox, demo_conversation):
    run = _run(demo_conversation, ["greeting", "price", "booking"])
    run.save()  # pipeline saves a run several times: still one event
    _run(demo_conversation, ["greeting"])
    rows = IntegrationOutbox.objects.filter(event_type="opportunity.suggested")
    assert rows.count() == 1
    assert rows.get().payload["payload"]["signals"] == ["booking", "price"]
    # the summary lists the intents as detected needs
    assert events.recent_intents(demo_conversation)[:3] == ["greeting", "price", "booking"]


@pytest.mark.parametrize(
    "reasons,expected",
    [
        (["handoff_requested"], "customer_asked_human"),
        (["sensitive:gia"], "sensitive_topic"),
        (["output:sensitive:hop_dong"], "sensitive_topic"),
        (["llm_error"], "ai_uncertain"),
    ],
)
def test_handoff_reason_mapping(sandbox, demo_conversation, reasons, expected):
    ApprovalQueue.objects.create(
        tenant=demo_conversation.tenant,
        kind="escalation",
        conversation=demo_conversation,
        contact=demo_conversation.contact,
        proposed_response="",
        reasons=reasons,
        owner_staff_ref="staff-01",
        expires_at=timezone.now() + timedelta(hours=1),
        data_origin="demo",
    )
    payload = IntegrationOutbox.objects.get(event_type="handoff.requested").payload["payload"]
    assert payload["reason"] == expected and payload["owner_staff_ref"] == "staff-01"


def test_draft_approvals_are_not_handoffs(sandbox, demo_conversation):
    ApprovalQueue.objects.create(
        tenant=demo_conversation.tenant,
        kind="draft",
        contact=demo_conversation.contact,
        proposed_response="x",
        expires_at=timezone.now() + timedelta(hours=1),
        data_origin="demo",
    )
    assert _types() == []


def test_profile_suggestion_only_for_important_crs_facts(sandbox, demo_contact, demo_channel):
    common = {"tenant": demo_contact.tenant, "data_origin": "demo"}
    LongTermMemory.objects.create(
        **common, contact=demo_contact, content="Có 30 nhân viên", importance=8
    )
    LongTermMemory.objects.create(**common, contact=demo_contact, content="Thích trà", importance=3)
    no_crs = ContactFactory(channel=demo_channel, data_origin="demo")
    no_crs.set_consent("granted", "crs")
    no_crs.save()
    LongTermMemory.objects.create(
        **common, contact=no_crs, content="Sắp mở chi nhánh", importance=9
    )
    row = IntegrationOutbox.objects.get(event_type="profile.update_suggested")
    assert row.payload["payload"]["suggested_changes"] == {
        "memory_fact": {"type": "fact", "content": "Có 30 nhân viên"}
    }


def test_producer_failure_never_breaks_caller(sandbox, demo_contact, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(events, "enqueue_event", boom)
    assert events.lead_inbound(demo_contact) is None


def test_full_pipeline_produces_events(
    sandbox, demo_contact, settings, monkeypatch, django_capture_on_commit_callbacks
):
    """Inbound message -> AI pipeline (mock LLM) -> lead + opportunity + summary."""
    from apps.ai_engine.llm import MockProvider

    settings.AI_ENGINE_ENABLED = True
    main = MockProvider(
        canned_reply="Dạ em gửi chị thông tin khóa học ạ.",
        canned_json={"detected_intents": ["price"], "confidence": 0.9},
    )
    fast = MockProvider(canned_json={"summary": "Chị hỏi giá khóa AI cho 30 người."})

    def factory(kind="main"):
        return fast if kind == "fast" else main

    for target in ("apps.ai_engine.pipeline", "apps.memory.services"):
        monkeypatch.setattr(f"{target}.get_llm_provider", factory)
    with django_capture_on_commit_callbacks(execute=True):
        handle_channel_event(
            demo_contact.channel, _event(demo_contact.zalo_user_id, "Cho chị xin giá")
        )
    assert {"lead.inbound", "opportunity.suggested"} <= set(_types())
    from apps.memory.services import update_memory

    update_memory(demo_contact.conversations.get(), force=True)
    assert "conversation.summary" in _types()
    assert all(row.data_origin == "demo" for row in IntegrationOutbox.objects.all())
