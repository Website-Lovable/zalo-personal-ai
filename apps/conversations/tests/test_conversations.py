import pytest

from apps.channels.providers.mock import MockChannelProvider
from apps.conversations.models import Message, PendingOwnerSend
from apps.conversations.services import ReplyNotAllowed, dispatch_reply
from common.models import AuditLog
from conftest import auth_client
from tests.factories import ChannelFactory, ContactFactory, ConversationFactory, MessageFactory

pytestmark = pytest.mark.django_db


def test_conversation_crud_and_messages(client_a, tenant_a):
    contact = ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    response = client_a.post(
        "/api/conversations/", {"channel": contact.channel_id, "contact": contact.pk}, format="json"
    )
    assert response.status_code == 201
    conv_id = response.json()["id"]
    assert response.json()["contact_name"] == contact.display_name

    patched = client_a.patch(
        f"/api/conversations/{conv_id}/",
        {"ai_enabled": False, "human_takeover": True},
        format="json",
    )
    assert patched.json()["ai_enabled"] is False and patched.json()["human_takeover"] is True

    conversation = contact.conversations.get()
    MessageFactory(conversation=conversation, content="Một")
    MessageFactory(conversation=conversation, content="Hai")
    messages = client_a.get(f"/api/conversations/{conv_id}/messages/").json()["results"]
    assert [m["content"] for m in messages] == ["Một", "Hai"]
    assert client_a.delete(f"/api/conversations/{conv_id}/").status_code == 204


def test_conversation_contact_must_match_channel(client_a, tenant_a):
    contact = ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    other_channel = ChannelFactory(tenant=tenant_a)
    response = client_a.post(
        "/api/conversations/", {"channel": other_channel.pk, "contact": contact.pk}, format="json"
    )
    assert response.status_code == 400


def test_dispatch_auto_channel_sends_via_provider(tenant_a):
    conversation = ConversationFactory(
        contact=ContactFactory(channel=ChannelFactory(tenant=tenant_a, mode="auto"))
    )
    result = dispatch_reply(conversation, "Chào bạn")
    assert result.sent and result.message.direction == Message.Direction.OUTBOUND
    assert MockChannelProvider.sent[0][2] == "Chào bạn"
    conversation.refresh_from_db()
    assert conversation.last_message_at is not None


def test_dispatch_assist_channel_queues_for_owner(client_a, owner_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, mode="assist")
    conversation = ConversationFactory(contact=ContactFactory(channel=channel))
    result = dispatch_reply(conversation, "Dạ em về ạ")
    assert not result.sent and result.pending.status == PendingOwnerSend.Status.PENDING
    assert MockChannelProvider.sent == []
    assert AuditLog.objects.filter(action="reply.queued_for_owner").exists()

    inbox = client_a.get("/api/owner-sends/?status=pending").json()
    assert inbox["count"] == 1 and inbox["results"][0]["draft_text"] == "Dạ em về ạ"
    pk = result.pending.pk
    response = client_a.post(f"/api/owner-sends/{pk}/mark-sent/")
    assert response.json()["status"] == "sent_by_owner"
    assert response.json()["handled_by"] == owner_a.pk
    assert client_a.post(f"/api/owner-sends/{pk}/dismiss/").status_code == 400


def test_owner_send_dismiss_and_viewer_forbidden(client_a, viewer_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, mode="assist")
    pending = dispatch_reply(
        ConversationFactory(contact=ContactFactory(channel=channel)), "x"
    ).pending
    assert auth_client(viewer_a).post(f"/api/owner-sends/{pending.pk}/dismiss/").status_code == 403
    assert client_a.post(f"/api/owner-sends/{pending.pk}/dismiss/").json()["status"] == "dismissed"


def test_dispatch_refuses_when_reply_not_allowed(tenant_a):
    contact = ContactFactory(
        channel=ChannelFactory(tenant=tenant_a), reply_permission="do_not_reply"
    )
    with pytest.raises(ReplyNotAllowed):
        dispatch_reply(ConversationFactory(contact=contact), "x")
    assert MockChannelProvider.sent == []


def test_personal_channel_never_messages_first(tenant_a):
    personal = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    contact = ContactFactory(channel=personal, has_messaged_first=False)
    with pytest.raises(ReplyNotAllowed):
        dispatch_reply(ConversationFactory(contact=contact), "x")


def test_dispatch_raises_when_provider_fails(tenant_a, monkeypatch):
    from apps.channels.providers.base import SendResult

    monkeypatch.setattr(
        MockChannelProvider, "send_text", lambda self, r, t: SendResult(ok=False, error="boom")
    )
    conversation = ConversationFactory(
        contact=ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    )
    with pytest.raises(RuntimeError):
        dispatch_reply(conversation, "x")
    assert not Message.objects.filter(direction="outbound").exists()
