"""Phase 4: dashboard API, analytics, activity, real-time notifications, admin UI shell."""

from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.staticfiles import finders
from django.core import signing
from django.utils import timezone

from apps.ai_engine.models import AIReplyLog, LLMUsage
from apps.dashboard.notifications import TICKET_SALT, make_ticket, read_ticket, tenant_group
from apps.permissions.models import ApprovalQueue
from common.audit import log_action
from config.asgi import application
from tests.factories import (
    ApprovalFactory,
    ChannelFactory,
    ContactFactory,
    ConversationFactory,
    MessageFactory,
    UserFactory,
)


@pytest.fixture
def data_a(tenant_a):
    channel = ChannelFactory(tenant=tenant_a, status="active")
    contact = ContactFactory(channel=channel, consent_status="granted", consent_source="crs")
    conversation = ConversationFactory(contact=contact)
    inbound = MessageFactory(conversation=conversation)
    MessageFactory(conversation=conversation, direction="outbound", is_ai_generated=True)
    MessageFactory(
        conversation=conversation, direction="outbound", is_ai_generated=True, was_approved=True
    )
    AIReplyLog.objects.create(
        tenant=tenant_a,
        conversation=conversation,
        inbound_message=inbound,
        outcome="sent",
        confidence=0.8,
    )
    ApprovalFactory(message=inbound, conversation=conversation, owner_staff_ref="staff-01")
    LLMUsage.objects.create(
        tenant=tenant_a, provider="mock", model="m", input_tokens=100, output_tokens=20
    )
    return conversation


# --- Dashboard API ---------------------------------------------------------------------


@pytest.mark.django_db
def test_stats(client_a, client_b, owner_a, data_a):
    owner_a.staff_ref = "staff-01"
    owner_a.save()
    stats = client_a.get("/api/dashboard/stats/").json()
    assert stats["channels_active"] == 1 and stats["contacts_with_consent"] == 1
    assert stats["messages_in_today"] == 1 and stats["messages_out_today"] == 2
    assert stats["ai_auto_sent_today"] == 1
    assert stats["pending_approvals"] == 1 and stats["my_pending_approvals"] == 1
    assert stats["emergency_stop"] is False
    other = client_b.get("/api/dashboard/stats/").json()
    assert other["messages_in_today"] == 0 and other["pending_approvals"] == 0


@pytest.mark.django_db
def test_analytics(client_a, client_b, data_a):
    data = client_a.get("/api/dashboard/analytics/?days=7").json()
    assert len(data["days"]) == 7 and data["days"][-1] == timezone.localdate().isoformat()
    assert data["messages"]["inbound"][-1] == 1
    assert data["messages"]["ai_sent"][-1] == 1 and data["messages"]["approved_sent"][-1] == 1
    assert data["ai_outcomes"]["sent"] == 1 and data["ai_avg_confidence"] == 0.8
    assert data["approvals"]["pending"] == 1 and data["llm"]["input"] == 100
    assert data["messages_by_channel_type"] == {"zalo_oa": 3}
    assert len(client_a.get("/api/dashboard/analytics/?days=500").json()["days"]) == 90
    assert len(client_a.get("/api/dashboard/analytics/?days=abc").json()["days"]) == 7
    assert sum(client_b.get("/api/dashboard/analytics/").json()["messages"]["inbound"]) == 0


@pytest.mark.django_db
def test_approval_review_time(client_a, data_a):
    item = ApprovalQueue.objects.get()
    item.reviewed_at = item.created_at + timedelta(minutes=12)
    item.status = "approved"
    item.save()
    assert client_a.get("/api/dashboard/analytics/").json()["approval_median_minutes"] == 12.0


@pytest.mark.django_db
def test_activity(client_a, client_b, tenant_a, owner_a):
    log_action("ai.emergency_stop", tenant=tenant_a, user=owner_a)
    log_action("auth.login", tenant=tenant_a, user=owner_a)  # not shown in the feed
    log_action("ai.something_new", tenant=tenant_a)  # unknown ai.* -> shown with its code
    items = client_a.get("/api/dashboard/activity/?limit=10").json()["results"]
    assert [i["label"] for i in items] == ["ai.something_new", "Dừng khẩn cấp AI"]
    assert items[1]["user"] == owner_a.username
    assert client_a.get("/api/dashboard/activity/?limit=x").status_code == 200
    assert client_b.get("/api/dashboard/activity/").json()["results"] == []


def test_dashboard_requires_login(client):
    for path in ("stats", "analytics", "activity"):
        assert client.get(f"/api/dashboard/{path}/").status_code == 401
    assert client.post("/api/notifications/ticket/").status_code == 401


# --- Admin UI shell ------------------------------------------------------------------


def test_app_shell_and_static(client):
    response = client.get("/app/")
    assert response.status_code == 200
    assert "default-src 'self'" in response["Content-Security-Policy"]
    assert "script-src 'self'" in response["Content-Security-Policy"]
    assert 'lang="vi"' in response.content.decode()
    assert client.get("/").url == "/app/"
    for name in ("app.js", "app.css", "icon.svg"):
        assert finders.find(f"dashboard/{name}")


# --- Real-time notifications -----------------------------------------------------------


@pytest.mark.django_db
def test_ticket_roundtrip(client_a, owner_a):
    ticket = client_a.post("/api/notifications/ticket/").json()
    assert ticket["ws_path"] == "/ws/notifications/" and ticket["expires_in"] == 60
    assert read_ticket(ticket["ticket"]) == {"u": owner_a.pk, "t": owner_a.tenant_id}
    assert read_ticket("forged") is None
    expired = signing.TimestampSigner(salt=TICKET_SALT)  # wrong format -> refused
    assert read_ticket(expired.sign("x")) is None


async def _connect(ticket):
    communicator = WebsocketCommunicator(
        application,
        f"/ws/notifications/?ticket={ticket}",
        headers=[(b"origin", b"http://localhost")],
    )
    connected, code = await communicator.connect()
    return communicator, connected, code


@pytest.mark.django_db(transaction=True)
def test_websocket_tenant_isolation_and_ping(tenant_a, tenant_b):
    user_a = UserFactory(tenant=tenant_a)
    user_b = UserFactory(tenant=tenant_b)

    async def scenario():
        comm_a, ok_a, _ = await _connect(make_ticket(user_a))
        comm_b, ok_b, _ = await _connect(make_ticket(user_b))
        assert ok_a and ok_b
        assert await comm_a.receive_json_from() == {"type": "hello"}
        assert await comm_b.receive_json_from() == {"type": "hello"}
        layer = get_channel_layer()
        await layer.group_send(
            tenant_group(tenant_a.pk), {"type": "notify", "event": {"type": "x", "id": 1}}
        )
        assert await comm_a.receive_json_from() == {"type": "x", "id": 1}
        assert await comm_b.receive_nothing()
        await comm_a.send_json_to({"type": "ping"})
        assert await comm_a.receive_json_from() == {"type": "pong"}
        await comm_a.disconnect()
        await comm_b.disconnect()

    async_to_sync(scenario)()


@pytest.mark.django_db(transaction=True)
def test_websocket_rejects_bad_tickets(tenant_a):
    inactive = UserFactory(tenant=tenant_a, is_active=False)

    async def scenario():
        for ticket in ("", "nonsense", make_ticket(inactive)):
            _, connected, _ = await _connect(ticket)
            assert not connected

    async_to_sync(scenario)()


@pytest.mark.django_db(transaction=True)
def test_model_events_are_broadcast(tenant_a):
    user = UserFactory(tenant=tenant_a)
    conversation = ConversationFactory(
        contact=ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    )

    async def listen():
        comm, ok, _ = await _connect(make_ticket(user))
        assert ok and await comm.receive_json_from() == {"type": "hello"}
        return comm

    def create():
        message = MessageFactory(conversation=conversation)  # committed -> on_commit fires
        ApprovalFactory(message=message, conversation=conversation, contact=conversation.contact)

    async def scenario():
        comm = await listen()
        await sync_to_async(create)()
        events = [await comm.receive_json_from(timeout=2) for _ in range(2)]
        await comm.disconnect()
        return events

    events = async_to_sync(scenario)()
    assert events[0]["type"] == "message.inbound"
    assert events[0]["conversation_id"] == conversation.pk
    assert "content" not in events[0]  # never the message text
    assert events[1]["type"] == "approval.created" and events[1]["status"] == "pending"
