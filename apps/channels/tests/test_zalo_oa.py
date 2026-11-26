import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.channels.models import Channel, OAuthState, WebhookEvent
from apps.channels.providers.http import ZaloAPIError, ZaloAuthError
from apps.channels.providers.zalo_oa import (
    ZaloOAProvider,
    compute_signature,
    new_pkce_pair,
    oa_id_from_payload,
)
from apps.channels.services import (
    ChannelConnectError,
    complete_oa_oauth,
    ensure_fresh_oa_token,
    process_event,
    refresh_expiring_oa_tokens,
    refresh_oa_token,
    sync_oa_contacts,
)
from apps.channels.testing.fake_zalo import FakeZalo
from apps.contacts.models import Contact
from apps.conversations.models import Conversation, Message
from common.models import AuditLog
from tests.factories import ChannelFactory, ContactFactory, ConversationFactory

pytestmark = pytest.mark.django_db


def _post_oa(api_client, payload, *, path="/webhooks/zalo-oa/", secret=None, signature=None):
    body = json.dumps(payload).encode()
    sig = signature if signature is not None else FakeZalo.sign(body, secret)
    return api_client.post(
        path, data=body, content_type="application/json", HTTP_X_ZEVENT_SIGNATURE=sig
    )


def _text_event(fake, user_id="u-1", text="Chào shop", msg_id="m-1", oa_id=None):
    return FakeZalo.oa_event(
        "user_send_text",
        sender={"id": user_id},
        recipient={"id": oa_id or fake.oa_id},
        message={"msg_id": msg_id, "text": text},
        user_id_by_app="app-user-1",
    )


# --- OAuth v4 + PKCE ---------------------------------------------------------------------


def test_pkce_pair_is_s256():
    verifier, challenge = new_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=") and "=" not in challenge
    assert 43 <= len(verifier) <= 128


def test_oauth_start_refused_in_mock_backend(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    response = client_a.post(f"/api/channels/{channel.pk}/oauth/start/")
    assert response.status_code == 400
    assert "giả lập" in response.json()["detail"]


def test_oauth_start_requires_owner(admin_a, tenant_a, fake_zalo):
    from conftest import auth_client

    oa = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    assert auth_client(admin_a).post(f"/api/channels/{oa.pk}/oauth/start/").status_code == 403


def test_oauth_full_flow(client, client_a, tenant_a, fake_zalo):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    response = client_a.post(f"/api/channels/{channel.pk}/oauth/start/")
    assert response.status_code == 200
    url = urlparse(response.json()["authorize_url"])
    assert url.netloc == "oauth.zaloapp.com" and url.path == "/v4/oa/permission"
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query["app_id"] == "test-app-id"
    assert query["redirect_uri"] == "https://zalo-ai.test/oauth/zalo-oa/callback/"
    pending = OAuthState.objects.get(channel=channel)
    assert pending.state == query["state"]

    code = fake_zalo.issue_code(query["code_challenge"])
    callback = client.get(
        "/oauth/zalo-oa/callback/",
        {"code": code, "oa_id": fake_zalo.oa_id, "state": query["state"]},
    )
    assert callback.status_code == 200 and "Kết nối thành công" in callback.content.decode()
    channel.refresh_from_db()
    assert channel.status == "active" and channel.zalo_oa_id == fake_zalo.oa_id
    assert channel.access_token.startswith("at-") and channel.refresh_token.startswith("rt-")
    assert channel.token_expires_at > timezone.now() + timedelta(hours=24)
    assert AuditLog.objects.filter(action="channel.connected", object_id=str(channel.pk)).exists()
    assert "at-" not in json.dumps(list(AuditLog.objects.values_list("changes", flat=True)))

    # The state is single use.
    again = client.get(
        "/oauth/zalo-oa/callback/", {"code": code, "oa_id": "x", "state": query["state"]}
    )
    assert again.status_code == 400


@override_settings(ZALO_OA_OAUTH_RESULT_URL="https://app.test/channels")
def test_oauth_callback_redirects_to_result_page(client, tenant_a, owner_a, fake_zalo):
    from apps.channels.services import start_oa_oauth

    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    query = parse_qs(urlparse(start_oa_oauth(channel, owner_a)).query)
    code = fake_zalo.issue_code(query["code_challenge"][0])
    response = client.get(
        "/oauth/zalo-oa/callback/",
        {"code": code, "oa_id": fake_zalo.oa_id, "state": query["state"][0]},
    )
    assert response.status_code == 302
    assert response["Location"].startswith("https://app.test/channels?status=success")
    bad = client.get("/oauth/zalo-oa/callback/", {"state": "nope"})
    assert "status=error" in bad["Location"]


def test_oauth_wrong_verifier_or_expired_state(tenant_a, owner_a, fake_zalo):
    from apps.channels.services import start_oa_oauth

    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    query = parse_qs(urlparse(start_oa_oauth(channel, owner_a)).query)
    state = query["state"][0]
    wrong_code = fake_zalo.issue_code("not-the-challenge")
    with pytest.raises(ChannelConnectError):
        complete_oa_oauth(state=state, code=wrong_code, oa_id=fake_zalo.oa_id)
    assert AuditLog.objects.filter(action="channel.oauth_failed").exists()

    query = parse_qs(urlparse(start_oa_oauth(channel, owner_a)).query)
    OAuthState.objects.filter(state=query["state"][0]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(ChannelConnectError):
        complete_oa_oauth(state=query["state"][0], code="c", oa_id=fake_zalo.oa_id)

    query = parse_qs(urlparse(start_oa_oauth(channel, owner_a)).query)
    with pytest.raises(ChannelConnectError):  # user denied consent: no code
        complete_oa_oauth(state=query["state"][0], code="", oa_id="")


def test_oauth_refuses_oa_already_connected_elsewhere(tenant_a, tenant_b, owner_a, fake_zalo):
    from apps.channels.services import start_oa_oauth

    ChannelFactory(tenant=tenant_b, channel_type="zalo_oa", zalo_oa_id=fake_zalo.oa_id)
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    query = parse_qs(urlparse(start_oa_oauth(channel, owner_a)).query)
    code = fake_zalo.issue_code(query["code_challenge"][0])
    with pytest.raises(ChannelConnectError, match="kênh khác"):
        complete_oa_oauth(state=query["state"][0], code=code, oa_id=fake_zalo.oa_id)


@override_settings(ZALO_OA_APP_SECRET="")
def test_oauth_start_needs_app_credentials(client_a, tenant_a, fake_zalo):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    response = client_a.post(f"/api/channels/{channel.pk}/oauth/start/")
    assert response.status_code == 400 and "ZALO_OA_APP_ID" in response.json()["detail"]


@override_settings(ZALO_OA_OAUTH_REDIRECT_URI="")
def test_oauth_start_needs_redirect_uri(client_a, tenant_a, fake_zalo):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    assert client_a.post(f"/api/channels/{channel.pk}/oauth/start/").status_code == 400


def test_oauth_start_only_for_oa(client_a, tenant_a, fake_zalo):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_bot")
    assert client_a.post(f"/api/channels/{channel.pk}/oauth/start/").status_code == 400


# --- Tokens -------------------------------------------------------------------------------


def test_token_refresh_when_expiring_and_refresh_token_single_use(oa_channel, fake_zalo):
    old_access, old_refresh = oa_channel.access_token, oa_channel.refresh_token
    Channel.objects.filter(pk=oa_channel.pk).update(
        token_expires_at=timezone.now() + timedelta(minutes=5)
    )
    oa_channel.refresh_from_db()
    token = ensure_fresh_oa_token(oa_channel)
    assert token != old_access
    oa_channel.refresh_from_db()
    assert oa_channel.refresh_token != old_refresh
    assert AuditLog.objects.filter(action="channel.token_refreshed").count() == 1

    # Not expiring: no refresh call.
    calls = len(fake_zalo.requests)
    assert ensure_fresh_oa_token(oa_channel) == oa_channel.access_token
    assert len(fake_zalo.requests) == calls

    # The old refresh token was spent; using it fails and disconnects the channel.
    Channel.objects.filter(pk=oa_channel.pk).update(refresh_token=old_refresh)
    oa_channel.refresh_from_db()
    with pytest.raises(ZaloAuthError):
        refresh_oa_token(oa_channel, force=True)
    oa_channel.refresh_from_db()
    assert oa_channel.status == "disconnected" and "Làm mới token" in oa_channel.last_error
    assert AuditLog.objects.filter(action="channel.token_refresh_failed").exists()


def test_no_access_token_raises(tenant_a, fake_zalo):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    with pytest.raises(ZaloAuthError):
        ensure_fresh_oa_token(channel)
    with pytest.raises(ZaloAuthError):
        refresh_oa_token(channel, force=True)  # no refresh token stored


def test_beat_job_refreshes_only_expiring_channels(oa_channel, tenant_a, fake_zalo):
    access, refresh = fake_zalo.issue_tokens()
    later = ChannelFactory(
        tenant=tenant_a,
        channel_type="zalo_oa",
        status="active",
        access_token=access,
        refresh_token=refresh,
        token_expires_at=timezone.now() + timedelta(days=1),
    )
    broken = ChannelFactory(
        tenant=tenant_a,
        channel_type="zalo_oa",
        status="active",
        access_token="x",
        refresh_token="rt-unknown",
        token_expires_at=timezone.now(),
    )
    Channel.objects.filter(pk=oa_channel.pk).update(token_expires_at=timezone.now())
    from apps.channels.tasks import refresh_expiring_oa_tokens as task

    assert task.delay() is not None
    assert refresh_expiring_oa_tokens() == {"refreshed": 0, "failed": 0}  # already done
    later.refresh_from_db()
    broken.refresh_from_db()
    assert later.access_token == access
    assert broken.status == "disconnected"


def test_refresh_token_api(client_a, oa_channel, fake_zalo):
    old = oa_channel.access_token
    response = client_a.post(f"/api/channels/{oa_channel.pk}/refresh-token/")
    assert response.status_code == 200
    oa_channel.refresh_from_db()
    assert oa_channel.access_token != old
    Channel.objects.filter(pk=oa_channel.pk).update(refresh_token="rt-bad")
    assert client_a.post(f"/api/channels/{oa_channel.pk}/refresh-token/").status_code == 400


# --- Sending --------------------------------------------------------------------------------


def test_send_text_and_token_retry(oa_channel, fake_zalo):
    fake_zalo.add_user("u-1", "Lan")
    provider = ZaloOAProvider(oa_channel)
    result = provider.send_text("u-1", "Xin chào")
    assert result.ok and result.message_id == "oa-msg-1"
    assert fake_zalo.oa_sent == [{"user_id": "u-1", "text": "Xin chào"}]

    # Zalo says the token expired: refresh once, retry once.
    fake_zalo.expire_access_tokens()
    result = provider.send_text("u-1", "Lần 2")
    assert result.ok and len(fake_zalo.oa_sent) == 2

    # Token rejected again even after refresh: give up with an auth error.
    fake_zalo.oa_fail_codes = [-216, -216]
    result = provider.send_text("u-1", "Lần 3")
    assert not result.ok and result.error_code == -216

    # Business errors are returned, not raised.
    result = provider.send_text("unknown-user", "Hi")
    assert not result.ok and result.error_code == -213
    assert not provider.send_text("u-1", "x" * 2001).ok
    assert not provider.send_text("u-1", "").ok


def test_reply_api_sends_through_oa(client_a, oa_channel, fake_zalo):
    fake_zalo.add_user("u-9", "Minh")
    contact = ContactFactory(channel=oa_channel, zalo_user_id="u-9")
    conversation = ConversationFactory(contact=contact)
    response = client_a.post(
        f"/api/conversations/{conversation.pk}/reply/", {"text": "Dạ em chào anh"}, format="json"
    )
    assert response.status_code == 201, response.json()
    assert response.json()["is_ai_generated"] is False
    assert fake_zalo.oa_sent[-1] == {"user_id": "u-9", "text": "Dạ em chào anh"}
    assert AuditLog.objects.filter(action="message.sent_by_staff").exists()

    fake_zalo.oa_fail_codes = [-230]
    response = client_a.post(
        f"/api/conversations/{conversation.pk}/reply/", {"text": "Lần 2"}, format="json"
    )
    assert response.status_code == 502
    assert AuditLog.objects.get(action="message.send_failed").changes["error_code"] == -230


def test_reply_api_respects_contact_rules(client_a, tenant_a, viewer_a):
    from conftest import auth_client

    contact = ContactFactory(
        channel=ChannelFactory(tenant=tenant_a), reply_permission="do_not_reply"
    )
    conversation = ConversationFactory(contact=contact)
    url = f"/api/conversations/{conversation.pk}/reply/"
    assert client_a.post(url, {"text": "x"}, format="json").status_code == 400
    assert auth_client(viewer_a).post(url, {"text": "x"}, format="json").status_code == 403


def test_reply_api_assist_mode_queues(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa", mode="assist")
    conversation = ConversationFactory(contact=ContactFactory(channel=channel))
    response = client_a.post(
        f"/api/conversations/{conversation.pk}/reply/", {"text": "x"}, format="json"
    )
    assert response.status_code == 202


# --- Contact sync ---------------------------------------------------------------------------


@override_settings(ZALO_OA_SYNC_PAGE_SIZE=2)
def test_contact_sync_paginates_and_never_grants_consent(oa_channel, fake_zalo):
    for i in range(5):
        fake_zalo.add_user(f"u-{i}", f"Khách {i}")
    existing = ContactFactory(
        channel=oa_channel,
        zalo_user_id="u-0",
        display_name="Old",
        crs_customer_id="crs-1",
        owner_staff_ref="sales-7",
    )
    stats = sync_oa_contacts(oa_channel)
    assert stats == {"created": 4, "updated": 1, "failed": 0}
    existing.refresh_from_db()
    assert existing.display_name == "Khách 0" and existing.crs_customer_id == "crs-1"
    assert existing.owner_staff_ref == "sales-7" and existing.is_follower is True
    synced = Contact.objects.filter(channel=oa_channel)
    assert synced.count() == 5
    assert not synced.filter(consent_status="granted").exists()
    assert synced.get(zalo_user_id="u-3").avatar_url.startswith("https://")
    assert AuditLog.objects.get(action="channel.contacts_synced").changes["created"] == 4


def test_contact_sync_api_queues_task(
    client_a, oa_channel, fake_zalo, django_capture_on_commit_callbacks
):
    fake_zalo.add_user("u-1", "A")
    with django_capture_on_commit_callbacks(execute=True):
        response = client_a.post(f"/api/channels/{oa_channel.pk}/sync-contacts/")
    assert response.status_code == 202
    assert Contact.objects.filter(channel=oa_channel, zalo_user_id="u-1").exists()


def test_contact_sync_counts_failures(oa_channel, fake_zalo, monkeypatch):
    fake_zalo.add_user("u-1", "A")

    def boom(self, user_id):
        raise ZaloAPIError("nope", code=-201)

    monkeypatch.setattr(ZaloOAProvider, "get_user_detail", boom)
    assert sync_oa_contacts(oa_channel)["failed"] == 1


def test_contact_sync_refused_in_mock_backend(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    assert client_a.post(f"/api/channels/{channel.pk}/sync-contacts/").status_code == 400


# --- Webhook: signature ------------------------------------------------------------------


def test_signature_formula_and_header_variants(tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    provider = ZaloOAProvider(channel)
    body = json.dumps({"app_id": "test-app-id", "timestamp": "123", "x": "é"}).encode()
    digest = hashlib.sha256(b"test-app-id" + body + b"123" + b"test-oa-secret-key").hexdigest()
    assert compute_signature("test-app-id", body, "123", "test-oa-secret-key") == digest
    assert provider.verify_webhook({"X-ZEvent-Signature": f"mac={digest}"}, body)
    assert provider.verify_webhook({"X-ZEvent-Signature": f"mac = {digest.upper()}"}, body)
    assert provider.verify_webhook({"X-ZEvent-Signature": digest}, body)
    assert not provider.verify_webhook({"X-ZEvent-Signature": "mac=" + "0" * 64}, body)
    assert not provider.verify_webhook({}, body)
    # Body changed after signing
    assert not provider.verify_webhook({"X-ZEvent-Signature": digest}, body + b" ")
    assert not provider.verify_webhook({"X-ZEvent-Signature": digest}, b"not json")
    assert not provider.verify_webhook({"X-ZEvent-Signature": digest}, b"[1]")
    # Event for another Zalo app
    other = json.dumps({"app_id": "other", "timestamp": "1"}).encode()
    sig = compute_signature("other", other, "1", "test-oa-secret-key")
    assert not provider.verify_webhook({"X-ZEvent-Signature": sig}, other)
    # Per-channel secret overrides the platform secret
    channel.webhook_secret = "own-secret"
    assert not provider.verify_webhook({"X-ZEvent-Signature": digest}, body)


@override_settings(ZALO_OA_WEBHOOK_SECRET="")
def test_no_secret_configured_rejects_everything(tenant_a):
    provider = ZaloOAProvider(ChannelFactory(tenant=tenant_a, channel_type="zalo_oa"))
    body = b'{"app_id":"test-app-id","timestamp":"1"}'
    assert not provider.verify_webhook(
        {"X-ZEvent-Signature": compute_signature("test-app-id", body, "1", "")}, body
    )


def test_oa_id_from_payload():
    assert oa_id_from_payload({"oa_id": "1", "recipient": {"id": "2"}}) == "1"
    assert oa_id_from_payload({"event_name": "user_send_text", "recipient": {"id": "2"}}) == "2"
    assert oa_id_from_payload({"event_name": "oa_send_text", "sender": {"id": "3"}}) == "3"
    assert oa_id_from_payload({}) == ""


# --- Webhook: parsing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_name", "message", "expected_type", "expected_text"),
    [
        ("user_send_text", {"msg_id": "a", "text": "hi"}, "text", "hi"),
        (
            "user_send_image",
            {"msg_id": "b", "attachments": [{"type": "image", "payload": {"url": "https://i"}}]},
            "image",
            "[Ảnh] https://i",
        ),
        (
            "user_send_file",
            {"msg_id": "c", "attachments": [{"payload": {"name": "bao-gia.pdf", "url": "u"}}]},
            "file",
            "[Tệp] bao-gia.pdf",
        ),
        (
            "user_send_location",
            {
                "msg_id": "d",
                "attachments": [{"payload": {"coordinates": {"latitude": 1, "longitude": 2}}}],
            },
            "location",
            "[Vị trí] 1,2",
        ),
        ("user_send_sticker", {"msg_id": "e", "attachments": []}, "sticker", "[Sticker]"),
        ("user_send_link", {"msg_id": "f"}, "other", "[Tin nhắn]"),
        ("user_send_newtype", {"msg_id": "g"}, "other", "[Tin nhắn]"),
    ],
)
def test_parse_message_events(tenant_a, event_name, message, expected_type, expected_text):
    provider = ZaloOAProvider(ChannelFactory(tenant=tenant_a, channel_type="zalo_oa"))
    payload = FakeZalo.oa_event(
        event_name, sender={"id": "u"}, recipient={"id": "oa"}, message=message
    )
    [event] = provider.parse_events(payload)
    assert event.kind == "message" and not event.outbound
    assert event.message.message_type == expected_type
    assert event.message.text == expected_text
    assert event.dedupe_key == message["msg_id"]
    assert event.message.sent_at.year == 2025
    assert provider.parse_webhook(payload)[0].sender_id == "u"


def test_parse_other_events(tenant_a):
    provider = ZaloOAProvider(ChannelFactory(tenant=tenant_a, channel_type="zalo_oa"))
    [follow] = provider.parse_events(
        FakeZalo.oa_event("follow", oa_id="oa", follower={"id": "u-5"}, source="oa_profile")
    )
    assert follow.kind == "follow" and follow.user_id == "u-5"
    [echo] = provider.parse_events(
        FakeZalo.oa_event(
            "oa_send_text", sender={"id": "oa"}, recipient={"id": "u"}, message={"msg_id": "z"}
        )
    )
    assert echo.outbound and echo.user_id == "u"
    assert (
        provider.parse_webhook(
            FakeZalo.oa_event("oa_send_text", sender={"id": "oa"}, recipient={"id": "u"})
        )
        == []
    )
    [seen] = provider.parse_events(FakeZalo.oa_event("user_seen_message", timestamp="bad"))
    assert seen.kind == "ignore"


# --- Webhook: endpoint + async processing ---------------------------------------------------


def test_webhook_end_to_end(client, oa_channel, fake_zalo, django_capture_on_commit_callbacks):
    fake_zalo.add_user("u-1", "Chị Lan")
    with django_capture_on_commit_callbacks(execute=True):
        response = _post_oa(client, _text_event(fake_zalo))
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    event = WebhookEvent.objects.get()
    assert event.status == "processed" and event.tenant == oa_channel.tenant
    contact = Contact.objects.get(channel=oa_channel, zalo_user_id="u-1")
    assert contact.has_messaged_first and contact.display_name == "Chị Lan"
    assert contact.consent_status == "unknown"  # messaging first is not consent to outreach
    message = Message.objects.get()
    assert message.direction == "inbound" and message.content == "Chào shop"
    assert message.zalo_message_id == "m-1" and message.tenant == oa_channel.tenant
    assert message.conversation.last_message_at == message.zalo_timestamp

    # Zalo retries the same delivery: stored once, processed once.
    with django_capture_on_commit_callbacks(execute=True):
        assert _post_oa(client, _text_event(fake_zalo)).status_code == 200
    assert WebhookEvent.objects.count() == 1 and Message.objects.count() == 1

    # A second message reuses the open conversation.
    with django_capture_on_commit_callbacks(execute=True):
        _post_oa(client, _text_event(fake_zalo, msg_id="m-2", text="Còn hàng không?"))
    assert Conversation.objects.count() == 1 and Message.objects.count() == 2


def test_webhook_signal_fires_for_phase3(
    client, oa_channel, fake_zalo, django_capture_on_commit_callbacks
):
    from apps.conversations.inbound import inbound_message_received

    received = []

    def listener(sender, message, conversation, channel, **kwargs):
        received.append(message.content)

    inbound_message_received.connect(listener)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            _post_oa(client, _text_event(fake_zalo))
    finally:
        inbound_message_received.disconnect(listener)
    assert received == ["Chào shop"]


def test_webhook_rejects_bad_signature_and_body(client, oa_channel, fake_zalo):
    response = _post_oa(client, _text_event(fake_zalo), signature="mac=" + "0" * 64)
    assert response.status_code == 401
    assert _post_oa(client, _text_event(fake_zalo), secret="wrong").status_code == 401
    response = client.post("/webhooks/zalo-oa/", data=b"{bad", content_type="application/json")
    assert response.status_code == 400
    assert not WebhookEvent.objects.exists()
    assert client.get("/webhooks/zalo-oa/").status_code == 200


def test_webhook_unknown_oa_is_acked_but_ignored(client, fake_zalo):
    response = _post_oa(client, _text_event(fake_zalo, oa_id="999"))
    assert response.status_code == 200 and response.json()["status"] == "ignored"
    assert not WebhookEvent.objects.exists()


def test_webhook_per_channel_url(client, oa_channel, fake_zalo, django_capture_on_commit_callbacks):
    path = f"/webhooks/zalo-oa/{oa_channel.webhook_key}/"
    with django_capture_on_commit_callbacks(execute=True):
        assert _post_oa(client, _text_event(fake_zalo), path=path).status_code == 200
    assert Message.objects.count() == 1
    assert _post_oa(client, _text_event(fake_zalo, oa_id="other"), path=path).status_code == 403
    missing = "/webhooks/zalo-oa/00000000-0000-0000-0000-000000000000/"
    assert _post_oa(client, _text_event(fake_zalo), path=missing).status_code == 404


def test_webhook_follow_unfollow_and_echo(
    client, oa_channel, fake_zalo, django_capture_on_commit_callbacks
):
    fake_zalo.add_user("u-7", "Anh Tú")
    follow = FakeZalo.oa_event("follow", oa_id=fake_zalo.oa_id, follower={"id": "u-7"})
    unfollow = FakeZalo.oa_event(
        "unfollow", oa_id=fake_zalo.oa_id, follower={"id": "u-7"}, timestamp="1764163300000"
    )
    echo = FakeZalo.oa_event(
        "oa_send_text",
        sender={"id": fake_zalo.oa_id},
        recipient={"id": "u-7"},
        message={"msg_id": "staff-1", "text": "Cảm ơn anh đã quan tâm"},
    )
    seen = FakeZalo.oa_event("user_seen_message", oa_id=fake_zalo.oa_id)
    with django_capture_on_commit_callbacks(execute=True):
        _post_oa(client, follow)
    contact = Contact.objects.get(zalo_user_id="u-7")
    assert contact.is_follower is True and not contact.has_messaged_first
    with django_capture_on_commit_callbacks(execute=True):
        _post_oa(client, unfollow)
        _post_oa(client, echo)
        _post_oa(client, seen)
    contact.refresh_from_db()
    assert contact.is_follower is False
    message = Message.objects.get()
    assert message.direction == "outbound" and message.zalo_message_id == "staff-1"
    assert WebhookEvent.objects.get(event_name="user_seen_message").status == "ignored"


def test_echo_of_our_own_api_send_is_not_duplicated(
    oa_channel, fake_zalo, client, django_capture_on_commit_callbacks
):
    from apps.conversations.services import dispatch_reply

    fake_zalo.add_user("u-1", "Lan")
    conversation = ConversationFactory(
        contact=ContactFactory(channel=oa_channel, zalo_user_id="u-1")
    )
    sent = dispatch_reply(conversation, "Dạ", is_ai_generated=False)
    echo = FakeZalo.oa_event(
        "oa_send_text",
        sender={"id": fake_zalo.oa_id},
        recipient={"id": "u-1"},
        message={"msg_id": sent.message.zalo_message_id, "text": "Dạ"},
    )
    with django_capture_on_commit_callbacks(execute=True):
        _post_oa(client, echo)
    assert Message.objects.count() == 1
    assert WebhookEvent.objects.get().status == "ignored"


def test_webhook_isolated_per_tenant(
    client, oa_channel, tenant_b, fake_zalo, django_capture_on_commit_callbacks
):
    other = ChannelFactory(tenant=tenant_b, channel_type="zalo_oa", zalo_oa_id="oa-b")
    with django_capture_on_commit_callbacks(execute=True):
        _post_oa(client, _text_event(fake_zalo, oa_id="oa-b", user_id="u-b"))
    assert Contact.objects.get(zalo_user_id="u-b").tenant == tenant_b
    assert not Contact.objects.filter(tenant=oa_channel.tenant).exists()
    assert Message.objects.get().tenant == tenant_b == other.tenant


def test_processing_failure_is_recorded_and_retried(oa_channel, fake_zalo, monkeypatch):
    from apps.channels import tasks
    from apps.channels.services import record_webhook_event

    event = record_webhook_event(oa_channel, _text_event(fake_zalo))

    def boom(channel, normalized):
        raise RuntimeError("db down")

    monkeypatch.setattr("apps.conversations.inbound.handle_channel_event", boom)
    process_event(event)
    event.refresh_from_db()
    assert event.status == "failed" and "db down" in event.error and event.attempts == 1
    assert not Message.objects.exists()

    retried = []
    monkeypatch.setattr(
        tasks.process_webhook_event, "retry", lambda **kw: retried.append(kw) or RuntimeError()
    )
    with pytest.raises(RuntimeError):
        tasks.process_webhook_event.run(event.pk)
    assert retried and retried[0]["countdown"] == 40

    monkeypatch.undo()
    with override_settings(ZALO_CHANNEL_BACKEND="mock"):
        assert tasks.process_webhook_event.run(event.pk) == "processed"
    assert tasks.process_webhook_event.run(event.pk) == "processed"  # idempotent
    assert tasks.process_webhook_event.run(999999) is None


# --- Misc endpoints ---------------------------------------------------------------------------


def test_webhook_info_and_disconnect(client_a, oa_channel):
    info = client_a.get(f"/api/channels/{oa_channel.pk}/webhook-info/").json()
    assert info["webhook_url"] == f"https://zalo-ai.test/webhooks/zalo-oa/{oa_channel.webhook_key}/"
    assert info["oauth_redirect_uri"].endswith("/oauth/zalo-oa/callback/")
    with override_settings(PUBLIC_BASE_URL=""):
        assert (
            client_a.get(f"/api/channels/{oa_channel.pk}/webhook-info/").json()["webhook_url"] == ""
        )
    response = client_a.post(f"/api/channels/{oa_channel.pk}/disconnect/")
    assert response.status_code == 200 and response.json()["status"] == "disconnected"
    oa_channel.refresh_from_db()
    assert oa_channel.access_token == "" and oa_channel.refresh_token == ""
    assert AuditLog.objects.filter(action="channel.disconnected").exists()


def test_webhook_secret_is_write_only_and_audited(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    response = client_a.patch(
        f"/api/channels/{channel.pk}/", {"webhook_secret": "own-oa-secret"}, format="json"
    )
    assert "webhook_secret" not in response.json()
    channel.refresh_from_db()
    assert channel.webhook_secret == "own-oa-secret"
    assert AuditLog.objects.get(action="channel.credentials_changed").changes == {
        "fields": ["webhook_secret"]
    }


def test_other_tenant_cannot_use_channel_actions(client_b, oa_channel):
    for path in ("oauth/start/", "sync-contacts/", "refresh-token/", "disconnect/"):
        assert client_b.post(f"/api/channels/{oa_channel.pk}/{path}").status_code == 404
    assert client_b.get(f"/api/channels/{oa_channel.pk}/webhook-info/").status_code == 404


@override_settings(ZALO_SITE_VERIFICATION_CODE="ABC123")
def test_zalo_site_verification(client):
    response = client.get("/zalo_verifierABC123.html")
    assert response.status_code == 200
    assert 'content="ABC123"' in response.content.decode()
    assert client.get("/zalo_verifierWRONG.html").status_code == 404
