"""Phase 5 end-to-end scenarios (Blueprint D5): sign-up -> connect Zalo OA -> customer writes
-> AI drafts -> human approves -> Zalo receives the reply, plus the safety switches.

Everything goes through public HTTP endpoints (API, Zalo webhooks, OAuth callback) with the
fake Zalo APIs and the MockProvider LLM, so the scenarios run in CI without any account."""

import pytest

from apps.ai_engine.models import AIReplyLog
from apps.conversations.models import Message
from common.models import AuditLog

pytestmark = pytest.mark.django_db


def _only(client, url):
    body = client.get(url).json()
    assert body["count"] == 1, body
    return body["results"][0]


def test_new_customer_draft_is_approved_and_delivered(stack):
    owner = stack.signup("Shop Hoa", "hoa_owner")
    stack.connect_oa(owner)
    stack.fake.add_user("u-1", "Chị Lan")

    stack.zalo_sends("u-1", "Chào shop, dạo này khỏe không?", "m-1")

    # Unknown contact: default permission is need_approval, nothing goes out yet.
    assert stack.sent_to("u-1") == []
    approval = _only(owner, "/api/approvals/?status=pending")
    assert approval["proposed_response"]
    contact = _only(owner, "/api/contacts/")
    assert contact["display_name"] == "Chị Lan" and contact["has_messaged_first"]
    assert contact["consent_status"] == "unknown"  # writing first is not outreach consent

    response = owner.post(f"/api/approvals/{approval['id']}/approve/", {}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "approved"
    assert stack.sent_to("u-1") == [approval["proposed_response"]]

    conversation = _only(owner, "/api/conversations/")
    messages = owner.get(f"/api/conversations/{conversation['id']}/messages/").json()
    rows = messages["results"] if isinstance(messages, dict) else messages
    assert [m["direction"] for m in rows] == ["inbound", "outbound"]
    for action in ("tenant.registered", "channel.connected", "approval.approved"):
        assert AuditLog.objects.filter(action=action).exists(), action


def test_auto_reply_sensitive_topics_and_emergency_stop(stack):
    owner = stack.signup("Shop Gốm", "gom_owner")
    stack.connect_oa(owner)
    stack.fake.add_user("u-2", "Anh Minh")
    stack.zalo_sends("u-2", "Chào em", "m-1")
    contact = _only(owner, "/api/contacts/")
    owner.post(f"/api/approvals/{_only(owner, '/api/approvals/')['id']}/reject/", {})

    # Owner trusts this contact: auto replies from now on.
    patched = owner.patch(
        f"/api/contacts/{contact['id']}/",
        {"reply_permission": "auto_reply", "relationship_type": "friend"},
        format="json",
    )
    assert patched.status_code == 200, patched.content
    stack.zalo_sends("u-2", "Cuối tuần này em có mở cửa không?", "m-2")
    assert len(stack.sent_to("u-2")) == 1

    # A price question is never auto-sent, whatever the contact permission says.
    stack.zalo_sends("u-2", "Bộ ấm chén này giá bao nhiêu vậy?", "m-3")
    assert len(stack.sent_to("u-2")) == 1
    pending = owner.get("/api/approvals/?status=pending").json()["results"]
    assert len(pending) == 1 and any(r.startswith("sensitive") for r in pending[0]["reasons"])

    # Emergency stop: the AI goes completely silent (not even a draft).
    stop = owner.post("/api/ai/emergency-stop/", {"reason": "kiểm tra"}, format="json")
    assert stop.status_code == 200 and stop.json()["emergency_stop"] is True
    stack.zalo_sends("u-2", "Em ơi?", "m-4")
    assert len(stack.sent_to("u-2")) == 1
    run = AIReplyLog.objects.get(inbound_message__zalo_message_id="m-4")
    assert run.outcome == "skipped"

    assert owner.post("/api/ai/resume/").status_code == 200
    stack.zalo_sends("u-2", "Em ơi, cho anh hỏi chút", "m-5")
    assert len(stack.sent_to("u-2")) == 2


def test_human_takeover_silences_ai_until_released(stack):
    owner = stack.signup("Shop Trà", "tra_owner")
    stack.connect_oa(owner)
    stack.fake.add_user("u-3", "Cô Hạnh")
    stack.zalo_sends("u-3", "Chào cháu", "m-1")
    contact = _only(owner, "/api/contacts/")
    owner.patch(f"/api/contacts/{contact['id']}/", {"reply_permission": "auto_reply"})
    conversation = _only(owner, "/api/conversations/")

    # Staff answer by hand: delivered, and the AI keeps quiet afterwards.
    reply = owner.post(
        f"/api/conversations/{conversation['id']}/reply/",
        {"text": "Dạ cháu chào cô ạ"},
        format="json",
    )
    assert reply.status_code == 201, reply.content
    assert stack.sent_to("u-3") == ["Dạ cháu chào cô ạ"]
    stack.zalo_sends("u-3", "Hôm nay có trà mới không cháu?", "m-2")
    assert stack.sent_to("u-3") == ["Dạ cháu chào cô ạ"]

    assert owner.post(f"/api/conversations/{conversation['id']}/release/").status_code == 200
    stack.zalo_sends("u-3", "Cháu ơi", "m-3")
    assert len(stack.sent_to("u-3")) == 2


def test_zalo_redelivery_never_double_replies(stack):
    owner = stack.signup("Shop Bánh", "banh_owner")
    stack.connect_oa(owner)
    stack.fake.add_user("u-4", "Bạn Vy")
    stack.zalo_sends("u-4", "Hi shop", "m-1")
    contact = _only(owner, "/api/contacts/")
    owner.patch(f"/api/contacts/{contact['id']}/", {"reply_permission": "auto_reply"})

    for _ in range(3):  # Zalo retries the same delivery
        stack.zalo_sends("u-4", "Shop còn bánh bông lan không?", "m-2")
    assert len(stack.sent_to("u-4")) == 1
    assert Message.objects.filter(zalo_message_id="m-2").count() == 1


def test_tenants_never_see_each_other_end_to_end(stack):
    owner_a = stack.signup("Shop A", "owner_a_e2e")
    stack.connect_oa(owner_a)
    stack.fake.add_user("u-5", "Khách của A")
    stack.zalo_sends("u-5", "Giá sản phẩm thế nào?", "m-1")

    owner_b = stack.signup("Shop B", "owner_b_e2e")
    for url in (
        "/api/channels/",
        "/api/contacts/",
        "/api/conversations/",
        "/api/approvals/",
        "/api/ai/runs/",
        "/api/owner-sends/",
        "/api/memories/",
    ):
        body = owner_b.get(url).json()
        assert body["count"] == 0, (url, body)
    approval_id = _only(owner_a, "/api/approvals/")["id"]
    assert owner_b.post(f"/api/approvals/{approval_id}/approve/").status_code == 404
    stats = owner_b.get("/api/dashboard/stats/").json()
    assert "Khách của A" not in str(stats)
    assert stack.sent_to("u-5") == []


def test_zalo_bot_channel_end_to_end(stack, settings):
    owner = stack.signup("Shop Bot", "bot_owner")
    created = owner.post(
        "/api/channels/",
        {"channel_type": "zalo_bot", "name": "Bot", "mode": "auto"},
        format="json",
    )
    channel_id = created.json()["id"]
    connect = owner.post(
        f"/api/channels/{channel_id}/bot/connect/",
        {"bot_token": "111:valid-bot-token"},
        format="json",
    )
    assert connect.status_code == 200, connect.content
    from apps.channels.models import Channel

    channel = Channel.objects.get(pk=channel_id)
    update = stack.fake.bot_update("chat-9", "Alo shop", message_id="b-1", name="Khách Bot")
    with stack.capture(execute=True):
        response = stack.public.post(
            f"/webhooks/zalo-bot/{channel.webhook_key}/",
            data=update,
            format="json",
            HTTP_X_BOT_API_SECRET_TOKEN=channel.webhook_secret,
        )
    assert response.status_code == 200, response.content
    contact = _only(owner, "/api/contacts/")
    assert contact["zalo_user_id"] == "chat-9"
    owner.patch(f"/api/contacts/{contact['id']}/", {"reply_permission": "auto_reply"})
    update = stack.fake.bot_update("chat-9", "Shop ơi", message_id="b-2", name="Khách Bot")
    with stack.capture(execute=True):
        stack.public.post(
            f"/webhooks/zalo-bot/{channel.webhook_key}/",
            data=update,
            format="json",
            HTTP_X_BOT_API_SECRET_TOKEN=channel.webhook_secret,
        )
    assert len(stack.fake.bot_sent) == 1
