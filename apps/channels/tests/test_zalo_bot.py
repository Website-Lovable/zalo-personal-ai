import json

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.channels.models import Channel, WebhookEvent
from apps.channels.providers.http import ZaloAPIError, ZaloAuthError, request_json
from apps.channels.providers.zalo_bot import SECRET_HEADER, ZaloBotClient, ZaloBotProvider
from apps.channels.services import poll_bot_once
from apps.channels.testing.fake_zalo import FakeZalo
from apps.contacts.models import Contact
from apps.conversations.models import Message
from common.models import AuditLog
from tests.factories import ChannelFactory, ContactFactory, ConversationFactory

pytestmark = pytest.mark.django_db


def _post_bot(api_client, channel, payload, secret="bot-secret-token-123"):
    headers = {"HTTP_X_BOT_API_SECRET_TOKEN": secret} if secret is not None else {}
    return api_client.post(
        f"/webhooks/zalo-bot/{channel.webhook_key}/",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


# --- Connect ----------------------------------------------------------------------------------


def test_bot_connect_webhook(client_a, tenant_a, fake_zalo):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_bot")
    response = client_a.post(
        f"/api/channels/{channel.pk}/bot/connect/",
        {"bot_token": "111:valid-bot-token", "delivery": "webhook"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "active" and body["has_access_token"]
    assert "bot_token" not in json.dumps(body) and "valid-bot-token" not in json.dumps(body)
    channel.refresh_from_db()
    assert channel.access_token == "111:valid-bot-token"
    assert channel.config["delivery"] == "webhook" and channel.config["bot_id"] == "9988"
    assert fake_zalo.bot_webhook == {
        "url": f"https://zalo-ai.test/webhooks/zalo-bot/{channel.webhook_key}/",
        "secret_token": channel.webhook_secret,
    }
    assert len(channel.webhook_secret) >= 32
    log = AuditLog.objects.get(action="channel.connected")
    assert "valid-bot-token" not in json.dumps(log.changes)


def test_bot_connect_polling_then_poll(
    client_a, tenant_a, fake_zalo, django_capture_on_commit_callbacks
):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_bot")
    fake_zalo.bot_webhook = {"url": "https://old", "secret_token": "x" * 8}
    response = client_a.post(
        f"/api/channels/{channel.pk}/bot/connect/",
        {"bot_token": "111:valid-bot-token", "delivery": "polling"},
        format="json",
    )
    assert response.status_code == 200
    assert fake_zalo.bot_webhook == {}  # getUpdates only works without a webhook
    channel.refresh_from_db()
    assert channel.webhook_secret == ""

    fake_zalo.bot_updates = [
        FakeZalo.bot_update("chat-1", "Alo", message_id="b-1")["result"],
        FakeZalo.bot_update("chat-1", "Alo", message_id="b-1")["result"],  # duplicate
    ]
    with django_capture_on_commit_callbacks(execute=True):
        assert poll_bot_once(channel) == 1
    assert WebhookEvent.objects.get().source == "polling"
    assert Message.objects.get().content == "Alo"
    with django_capture_on_commit_callbacks(execute=True):
        assert poll_bot_once(channel) == 0  # nothing new


def test_bot_connect_rejects_bad_token(client_a, tenant_a, fake_zalo):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_bot")
    response = client_a.post(
        f"/api/channels/{channel.pk}/bot/connect/", {"bot_token": "nope"}, format="json"
    )
    assert response.status_code == 400
    channel.refresh_from_db()
    assert channel.status == "disconnected" and channel.access_token == ""
    assert AuditLog.objects.filter(action="channel.connect_failed").exists()


def test_bot_connect_guards(client_a, admin_a, tenant_a, fake_zalo):
    from conftest import auth_client

    bot = ChannelFactory(tenant=tenant_a, channel_type="zalo_bot")
    oa = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    payload = {"bot_token": "111:valid-bot-token"}
    assert client_a.post(f"/api/channels/{oa.pk}/bot/connect/", payload).status_code == 400
    assert (
        auth_client(admin_a).post(f"/api/channels/{bot.pk}/bot/connect/", payload).status_code
        == 403
    )
    with override_settings(PUBLIC_BASE_URL=""):
        response = client_a.post(f"/api/channels/{bot.pk}/bot/connect/", payload)
    assert response.status_code == 400 and "PUBLIC_BASE_URL" in response.json()["detail"]


def test_bot_connect_refused_in_mock_backend(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_bot")
    response = client_a.post(
        f"/api/channels/{channel.pk}/bot/connect/", {"bot_token": "t"}, format="json"
    )
    assert response.status_code == 400


def test_bot_disconnect_deletes_webhook(client_a, bot_channel, fake_zalo):
    fake_zalo.bot_webhook = {"url": "https://x", "secret_token": "y" * 8}
    assert client_a.post(f"/api/channels/{bot_channel.pk}/disconnect/").status_code == 200
    assert fake_zalo.bot_webhook == {}
    bot_channel.refresh_from_db()
    assert bot_channel.access_token == "" and bot_channel.webhook_secret == ""


def test_bot_disconnect_tolerates_zalo_error(client_a, bot_channel, fake_zalo):
    fake_zalo.bot_tokens.clear()
    assert client_a.post(f"/api/channels/{bot_channel.pk}/disconnect/").status_code == 200


# --- Webhook ----------------------------------------------------------------------------------


def test_bot_webhook_end_to_end(client, bot_channel, fake_zalo, django_capture_on_commit_callbacks):
    update = FakeZalo.bot_update("chat-42", "Xin chào", message_id="2d75", name="Ted")
    with django_capture_on_commit_callbacks(execute=True):
        response = _post_bot(client, bot_channel, update)
    assert response.status_code == 200
    contact = Contact.objects.get(channel=bot_channel)
    assert contact.zalo_user_id == "chat-42" and contact.display_name == "Ted"
    assert contact.has_messaged_first
    message = Message.objects.get()
    assert message.content == "Xin chào" and message.zalo_message_id == "2d75"
    with django_capture_on_commit_callbacks(execute=True):
        _post_bot(client, bot_channel, update)  # retry
    assert Message.objects.count() == 1


def test_bot_webhook_secret_checks(client, bot_channel):
    update = FakeZalo.bot_update("c", "x", message_id="1")
    assert _post_bot(client, bot_channel, update, secret="wrong-secret").status_code == 403
    assert _post_bot(client, bot_channel, update, secret=None).status_code == 403
    Channel.objects.filter(pk=bot_channel.pk).update(webhook_secret="")
    bot_channel.refresh_from_db()
    assert _post_bot(client, bot_channel, update, secret="").status_code == 403
    assert not WebhookEvent.objects.exists()
    missing = client.post(
        "/webhooks/zalo-bot/00000000-0000-0000-0000-000000000000/",
        data="{}",
        content_type="application/json",
    )
    assert missing.status_code == 404


def test_bot_webhook_bad_body_and_inactive_channel(client, bot_channel):
    response = client.post(
        f"/webhooks/zalo-bot/{bot_channel.webhook_key}/",
        data="[]",
        content_type="application/json",
        HTTP_X_BOT_API_SECRET_TOKEN="bot-secret-token-123",
    )
    assert response.status_code == 400
    Channel.objects.filter(pk=bot_channel.pk).update(is_active=False)
    bot_channel.refresh_from_db()
    assert (
        _post_bot(client, bot_channel, FakeZalo.bot_update("c", "x", message_id="1")).status_code
        == 200
    )
    assert not WebhookEvent.objects.exists()


def test_bot_parse_events(bot_channel):
    provider = ZaloBotProvider(bot_channel)
    image = FakeZalo.bot_update("c", "", message_id="i1")
    image["result"]["event_name"] = "message.image.received"
    image["result"]["message"]["photo"] = "https://img"
    [event] = provider.parse_events(image)
    assert event.message.message_type == "image" and event.message.text == "[Ảnh] https://img"

    group = FakeZalo.bot_update("g", "hi", message_id="g1")
    group["result"]["message"]["chat"]["chat_type"] = "GROUP"
    assert provider.parse_events(group)[0].kind == "ignore"

    unsupported = FakeZalo.bot_update("c", "", message_id="u1")
    unsupported["result"]["event_name"] = "message.unsupported.received"
    assert provider.parse_events(unsupported)[0].kind == "ignore"
    assert provider.parse_events({"result": "x"}) == []
    assert provider.parse_webhook(FakeZalo.bot_update("c", "t", message_id="m"))[0].text == "t"


def test_bot_unsupported_event_is_ignored(client, bot_channel, django_capture_on_commit_callbacks):
    update = FakeZalo.bot_update("c", "", message_id="s1")
    update["result"]["event_name"] = "message.unsupported.received"
    with django_capture_on_commit_callbacks(execute=True):
        _post_bot(client, bot_channel, update)
    assert WebhookEvent.objects.get().status == "ignored"
    assert not Message.objects.exists()


def test_bot_verify_header_name(bot_channel):
    provider = ZaloBotProvider(bot_channel)
    assert SECRET_HEADER == "X-Bot-Api-Secret-Token"
    assert provider.verify_webhook({SECRET_HEADER: "bot-secret-token-123"}, b"")


# --- Sending ----------------------------------------------------------------------------------


def test_bot_send_message(bot_channel, fake_zalo):
    provider = ZaloBotProvider(bot_channel)
    result = provider.send_text("chat-1", "Chào bạn")
    assert result.ok and result.message_id == "bot-msg-1"
    assert fake_zalo.bot_sent == [{"chat_id": "chat-1", "text": "Chào bạn"}]
    assert not provider.send_text("chat-1", "x" * 2001).ok
    fake_zalo.bot_tokens.clear()
    result = provider.send_text("chat-1", "x")
    assert not result.ok and result.error_code == 401


def test_bot_reply_api(client_a, bot_channel, fake_zalo):
    conversation = ConversationFactory(
        contact=ContactFactory(channel=bot_channel, zalo_user_id="chat-5")
    )
    response = client_a.post(
        f"/api/conversations/{conversation.pk}/reply/", {"text": "Ok ạ"}, format="json"
    )
    assert response.status_code == 201
    assert fake_zalo.bot_sent == [{"chat_id": "chat-5", "text": "Ok ạ"}]


def test_bot_client_misc(fake_zalo):
    with pytest.raises(ZaloAuthError):
        ZaloBotClient("")
    client = ZaloBotClient("111:valid-bot-token")
    assert client.get_webhook_info() == {"url": "", "updated_at": 1749633372026}
    with pytest.raises(ZaloAPIError):
        client.call("noSuchMethod")
    with pytest.raises(ZaloAPIError):
        client.set_webhook("http://insecure", "short")
    fake_zalo.bot_webhook = {"url": "https://x", "secret_token": "s" * 8}
    with pytest.raises(ZaloAPIError) as exc:
        client.get_updates(1)
    assert exc.value.code == 409


def test_http_errors_are_wrapped():
    import httpx

    from apps.channels.providers.http import use_transport

    def network_down(request):
        raise httpx.ConnectError("down")

    with use_transport(httpx.MockTransport(network_down)), pytest.raises(ZaloAPIError):
        request_json("GET", "https://openapi.zalo.me/x")
    html = httpx.MockTransport(lambda r: httpx.Response(502, text="<html>"))
    with use_transport(html), pytest.raises(ZaloAPIError) as exc:
        request_json("GET", "https://openapi.zalo.me/x")
    assert exc.value.code == 502


# --- Polling jobs -----------------------------------------------------------------------------


def test_poll_task_and_command(bot_channel, fake_zalo, django_capture_on_commit_callbacks):
    from apps.channels.tasks import poll_bot_channels

    Channel.objects.filter(pk=bot_channel.pk).update(config={"delivery": "polling"})
    fake_zalo.bot_updates = [FakeZalo.bot_update("c", "a", message_id="p1")["result"]]
    with django_capture_on_commit_callbacks(execute=True):
        assert poll_bot_channels() == 1
    fake_zalo.bot_updates = [FakeZalo.bot_update("c", "b", message_id="p2")["result"]]
    with django_capture_on_commit_callbacks(execute=True):
        call_command("zalo_bot_poll", "--once", "--channel", str(bot_channel.pk))
    assert Message.objects.count() == 2

    fake_zalo.bot_tokens.clear()
    assert poll_bot_channels() == 0  # errors are logged, not raised
    call_command("zalo_bot_poll", "--once")


def test_poll_task_skips_in_mock_backend(bot_channel):
    from apps.channels.tasks import poll_bot_channels

    assert poll_bot_channels() == 0


def test_poll_task_respects_lock(bot_channel, fake_zalo):
    from django.core.cache import cache

    from apps.channels.tasks import poll_bot_channels

    Channel.objects.filter(pk=bot_channel.pk).update(config={"delivery": "polling"})
    cache.add(f"zalo-bot-poll-{bot_channel.pk}", 1)
    try:
        fake_zalo.bot_updates = [FakeZalo.bot_update("c", "a", message_id="p1")["result"]]
        assert poll_bot_channels() == 0
    finally:
        cache.delete(f"zalo-bot-poll-{bot_channel.pk}")
