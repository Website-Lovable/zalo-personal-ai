"""API: emergency stop, takeover, approvals workflow, preview, memories, AI runs."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ai_engine import pipeline
from apps.ai_engine.models import AIControl
from apps.ai_engine.tasks import expire_approvals
from apps.channels.providers.base import SendResult
from apps.channels.providers.mock import MockChannelProvider
from apps.memory.models import ShortTermMemory
from apps.permissions.models import ApprovalQueue
from common.models import AuditLog
from conftest import auth_client
from tests.factories import (
    ApprovalFactory,
    ChannelFactory,
    ContactFactory,
    ConversationFactory,
    LongTermMemoryFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


# --- emergency stop -------------------------------------------------------------------------


def test_emergency_stop_and_resume(client_a, admin_a, viewer_a, tenant_a):
    assert client_a.get("/api/ai/control/").json()["emergency_stop"] is False
    assert auth_client(viewer_a).post("/api/ai/emergency-stop/").status_code == 403
    response = auth_client(admin_a).post(
        "/api/ai/emergency-stop/", {"reason": "Khách phàn nàn"}, format="json"
    )
    assert response.status_code == 200 and response.json()["emergency_stop"] is True
    assert AIControl.objects.get(tenant=tenant_a).stopped_by == admin_a
    assert auth_client(admin_a).post("/api/ai/resume/").status_code == 403
    assert client_a.post("/api/ai/resume/").json()["emergency_stop"] is False
    actions = set(AuditLog.objects.for_tenant(tenant_a).values_list("action", flat=True))
    assert {"ai.emergency_stop", "ai.resumed"} <= actions


def test_ai_control_patch_owner_only(client_a, admin_a):
    url = "/api/ai/control/"
    payload = {"disclose_ai_when_asked": False, "emergency_stop": True}
    assert auth_client(admin_a).patch(url, payload, format="json").status_code == 403
    data = client_a.patch(url, payload, format="json").json()
    assert data["disclose_ai_when_asked"] is False and data["emergency_stop"] is False


def test_emergency_stop_is_per_tenant(client_a, client_b, tenant_a, tenant_b):
    client_a.post("/api/ai/emergency-stop/")
    assert AIControl.for_tenant(tenant_a).emergency_stop
    assert not AIControl.for_tenant(tenant_b).emergency_stop
    assert client_b.get("/api/ai/control/").json()["emergency_stop"] is False


# --- human takeover -------------------------------------------------------------------------


def _conversation(tenant, **contact_kwargs):
    channel = ChannelFactory(tenant=tenant, status="active")
    return ConversationFactory(contact=ContactFactory(channel=channel, **contact_kwargs))


def test_takeover_and_release(client_a, owner_a, tenant_a):
    conversation = _conversation(tenant_a)
    url = f"/api/conversations/{conversation.pk}/"
    data = client_a.post(url + "takeover/", {"minutes": 60}, format="json").json()
    assert data["human_takeover"] is True and data["takeover_by"] == owner_a.pk
    assert data["takeover_until"] is not None
    data = client_a.post(url + "takeover/", {}, format="json").json()
    assert data["takeover_until"] is None
    assert client_a.post(url + "release/").json()["human_takeover"] is False
    assert AuditLog.objects.filter(action="conversation.takeover_released").exists()


def test_takeover_cross_tenant_is_404(client_b, tenant_a):
    conversation = _conversation(tenant_a)
    assert client_b.post(f"/api/conversations/{conversation.pk}/takeover/").status_code == 404


def test_staff_reply_pauses_ai(client_a, tenant_a):
    conversation = _conversation(tenant_a)
    response = client_a.post(
        f"/api/conversations/{conversation.pk}/reply/", {"text": "Anh trả lời"}, format="json"
    )
    assert response.status_code == 201
    conversation.refresh_from_db()
    assert conversation.takeover_active()
    # an open-ended manual takeover is not shortened by a later staff reply
    conversation.takeover_until = None
    conversation.save()
    client_a.post(f"/api/conversations/{conversation.pk}/reply/", {"text": "Nữa"}, format="json")
    conversation.refresh_from_db()
    assert conversation.takeover_until is None


# --- approvals ------------------------------------------------------------------------------


def _approval(tenant, **kwargs):
    conversation = _conversation(tenant)
    return ApprovalFactory(
        message__conversation=conversation,
        conversation=conversation,
        contact=conversation.contact,
        **kwargs,
    )


def test_approve_sends_draft(client_a, owner_a, tenant_a):
    item = _approval(tenant_a)
    response = client_a.post(f"/api/approvals/{item.pk}/approve/", {}, format="json")
    assert response.status_code == 200 and response.json()["status"] == "approved"
    item.refresh_from_db()
    sent = item.sent_message
    assert sent.content == item.proposed_response and sent.was_approved is True
    assert sent.approved_by == owner_a and sent.is_ai_generated
    assert MockChannelProvider.sent[-1][2] == item.proposed_response


def test_approve_with_edit_and_reject(client_a, tenant_a):
    item = _approval(tenant_a)
    data = client_a.post(
        f"/api/approvals/{item.pk}/approve/", {"modified_response": "Bản sửa"}, format="json"
    ).json()
    assert data["status"] == "modified" and MockChannelProvider.sent[-1][2] == "Bản sửa"
    other = _approval(tenant_a)
    data = client_a.post(
        f"/api/approvals/{other.pk}/reject/", {"reason": "không phù hợp"}, format="json"
    ).json()
    assert data["status"] == "rejected"
    assert client_a.post(f"/api/approvals/{other.pk}/approve/").status_code == 400


def test_escalation_without_draft_needs_text(client_a, tenant_a):
    item = _approval(tenant_a, proposed_response="", kind="escalation")
    assert client_a.post(f"/api/approvals/{item.pk}/approve/").status_code == 400
    item.refresh_from_db()
    assert item.status == "pending"


def test_send_failure_returns_502_and_stays_pending(client_a, tenant_a, monkeypatch):
    monkeypatch.setattr(
        MockChannelProvider,
        "send_text",
        lambda self, rid, text: SendResult(ok=False, error="user blocked", error_code=-213),
    )
    item = _approval(tenant_a)
    response = client_a.post(f"/api/approvals/{item.pk}/approve/")
    assert response.status_code == 502
    item.refresh_from_db()
    assert item.status == "pending" and item.send_error == "user blocked"
    assert response.json()["approval"]["send_error"] == "user blocked"


def test_contact_not_allowed_blocks_approval(client_a, tenant_a):
    item = _approval(tenant_a)
    item.contact.reply_permission = "do_not_reply"
    item.contact.save()
    assert client_a.post(f"/api/approvals/{item.pk}/approve/").status_code == 400
    item.refresh_from_db()
    assert item.status == "pending"


def test_assigned_staff_can_decide_and_filter_mine(tenant_a):
    item = _approval(tenant_a, owner_staff_ref="sale-07")
    _approval(tenant_a, owner_staff_ref="sale-99")
    staff = auth_client(UserFactory(tenant=tenant_a, role="viewer", staff_ref="sale-07"))
    stranger = auth_client(UserFactory(tenant=tenant_a, role="viewer"))
    mine = staff.get("/api/approvals/?mine=1").json()["results"]
    assert [r["id"] for r in mine] == [item.pk]
    assert stranger.get("/api/approvals/?mine=1").json()["results"] == []
    assert stranger.post(f"/api/approvals/{item.pk}/approve/").status_code == 403
    assert staff.post(f"/api/approvals/{item.pk}/approve/").status_code == 200


def test_aiva_outreach_approval_needs_consent_then_sends(client_a, tenant_a):
    # Phase 3.5: approved Sales AI outreach is sent, but only to a consented contact.
    conversation = _conversation(tenant_a)
    item = ApprovalFactory(
        tenant=tenant_a,
        message=None,
        contact=conversation.contact,
        source="aiva_outreach",
    )
    response = client_a.post(f"/api/approvals/{item.pk}/approve/")
    assert response.status_code == 400 and MockChannelProvider.sent == []
    item.refresh_from_db()
    assert item.status == "pending"
    item.contact.set_consent("granted", "crs")
    item.contact.save()
    assert client_a.post(f"/api/approvals/{item.pk}/approve/").json()["status"] == "approved"
    assert MockChannelProvider.sent[-1][2] == item.proposed_response


def test_approvals_tenant_isolation(client_b, tenant_a):
    item = _approval(tenant_a)
    assert client_b.get("/api/approvals/").json()["results"] == []
    assert client_b.post(f"/api/approvals/{item.pk}/approve/").status_code == 404


def test_expire_task(tenant_a):
    overdue = _approval(tenant_a, expires_at=timezone.now() - timedelta(minutes=1))
    fresh = _approval(tenant_a)
    conversation = fresh.conversation
    ShortTermMemory.objects.create(
        tenant=tenant_a,
        conversation=conversation,
        context_summary="cũ",
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    assert expire_approvals() == {"approvals_expired": 1, "short_term_purged": 1}
    overdue.refresh_from_db()
    fresh.refresh_from_db()
    assert overdue.status == "expired" and fresh.status == "pending"


# --- preview, runs, memories ----------------------------------------------------------------


def test_preview_endpoint(client_a, client_b, viewer_a, tenant_a, llm):
    conversation = _conversation(tenant_a, reply_permission="auto_reply")
    from tests.factories import MessageFactory

    MessageFactory(conversation=conversation, content="Chào anh")
    payload = {"conversation": conversation.pk}
    assert auth_client(viewer_a).post("/api/ai/preview/", payload, format="json").status_code == 403
    assert client_b.post("/api/ai/preview/", payload, format="json").status_code == 404
    data = client_a.post("/api/ai/preview/", payload, format="json").json()
    assert data["action"] == "auto_reply" and data["draft"]
    from apps.ai_engine.llm import LLMError

    llm.error = LLMError("boom")
    assert client_a.post("/api/ai/preview/", payload, format="json").status_code == 502


def test_ai_runs_listing_is_tenant_scoped(client_a, client_b, tenant_a, llm):
    conversation = _conversation(tenant_a, reply_permission="auto_reply")
    from tests.factories import MessageFactory

    message = MessageFactory(conversation=conversation, content="Chào anh")
    pipeline.process_inbound_message(message.pk)
    runs = client_a.get("/api/ai/runs/").json()["results"]
    assert len(runs) == 1 and runs[0]["outcome"] == "sent"
    assert client_b.get("/api/ai/runs/").json()["results"] == []


def test_memories_crud_and_isolation(client_a, client_b, viewer_a, tenant_a, tenant_b):
    contact = ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    memory = LongTermMemoryFactory(contact=contact)
    other_contact = ContactFactory(channel=ChannelFactory(tenant=tenant_b))
    assert len(client_a.get("/api/memories/").json()["results"]) == 1
    assert client_b.get("/api/memories/").json()["results"] == []
    response = client_a.patch(
        f"/api/memories/{memory.pk}/", {"content": "Thích trà", "importance": 9}, format="json"
    )
    assert response.status_code == 200 and response.json()["importance"] == 9
    assert (
        client_a.patch(f"/api/memories/{memory.pk}/", {"importance": 11}, format="json").status_code
        == 400
    )
    created = client_a.post(
        "/api/memories/", {"contact": other_contact.pk, "content": "x"}, format="json"
    )
    assert created.status_code == 400  # tenant B's contact is not selectable
    assert auth_client(viewer_a).delete(f"/api/memories/{memory.pk}/").status_code == 403
    assert client_b.delete(f"/api/memories/{memory.pk}/").status_code == 404
    assert client_a.delete(f"/api/memories/{memory.pk}/").status_code == 204
    assert not ApprovalQueue.objects.exists()
