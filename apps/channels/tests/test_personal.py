"""Phase 2b: personal Zalo channel (isolated provider, guard, kill switch, auto-stop)."""

import json
import time
from datetime import timedelta

import httpx
import pytest
from django.core import mail
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from apps.channels.models import Channel, ChannelRiskAcknowledgement
from apps.channels.providers import get_provider
from apps.channels.providers.http import use_transport
from apps.channels.providers.personal_unofficial import control
from apps.channels.providers.personal_unofficial.adapter import (
    LoginState,
    RestrictionDetected,
    SidecarAdapter,
    SidecarError,
)
from apps.channels.providers.personal_unofficial.fake import FakePersonalAdapter
from apps.channels.providers.personal_unofficial.limits import (
    check_send,
    human_delay_seconds,
    in_window,
    parse_window,
)
from apps.channels.providers.personal_unofficial.provider import sign
from apps.conversations.models import Message
from apps.conversations.services import ReplyNotAllowed, SendFailed, dispatch_reply
from common.exceptions import FeatureDisabledError
from common.models import AuditLog
from conftest import auth_client
from tests.factories import (
    ChannelFactory,
    ContactFactory,
    ConversationFactory,
    MessageFactory,
)

SECRET = "test-sidecar-secret"  # matches config/settings/test.py

ENABLED = override_settings(
    ZALO_PERSONAL_UNOFFICIAL_ENABLED=True,
    ZALO_PERSONAL_SEND_WINDOW="00:00-00:00",
)


@pytest.fixture(autouse=True)
def _reset_fake():
    FakePersonalAdapter.reset()
    yield
    FakePersonalAdapter.reset()


@pytest.fixture
def enabled():
    with ENABLED:
        yield


def acknowledge(channel, user):
    return ChannelRiskAcknowledgement.objects.create(
        tenant=channel.tenant,
        channel=channel,
        acknowledged_by=user,
        full_name="Nguyễn Văn Chủ",
        statement_text="...",
    )


@pytest.fixture
def personal(tenant_a, owner_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal", name="Zalo của chủ")
    acknowledge(channel, owner_a)
    return channel


@pytest.fixture
def live(personal):
    """Logged-in, active, auto-mode channel."""
    personal.status = Channel.Status.ACTIVE
    personal.session_data = "fake-session"
    personal.config = {"zalo_account_id": "fake-zalo-account-1"}
    personal.mode = "auto"
    personal.save()
    return personal


def inbound(conversation, minutes_ago=1.0):
    message = MessageFactory(conversation=conversation, direction="inbound")
    Message.objects.filter(pk=message.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes_ago)
    )
    return message


def app_sent(conversation, minutes_ago=0.5, n=1):
    for _ in range(n):
        message = MessageFactory(
            conversation=conversation,
            direction="outbound",
            idempotency_key=f"out-test-{time.monotonic_ns()}",
        )
        Message.objects.filter(pk=message.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago)
        )


@pytest.fixture
def conversation(live):
    contact = ContactFactory(channel=live, zalo_user_id="zuid-khach-1")
    conv = ConversationFactory(contact=contact)
    inbound(conv)
    return conv


# --- Flag and isolation -----------------------------------------------------------------


def test_everything_refused_while_flag_off(client_a, personal):
    with pytest.raises(FeatureDisabledError):
        get_provider(personal)
    assert client_a.post(f"/api/personal-channels/{personal.pk}/login/start/").status_code == 404
    response = client_a.post(f"/webhooks/zalo-personal/{personal.webhook_key}/", {}, "json")
    assert response.status_code == 404
    # The emergency stop always works, even with the flag off.
    response = client_a.post(f"/api/personal-channels/{personal.pk}/emergency-stop/")
    assert response.status_code == 200


def test_core_modules_do_not_import_the_personal_package():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    allowed = {"providers/personal_unofficial", "channels/tests", "channels/providers/__init__.py"}
    for path in root.rglob("*.py"):
        rel = path.as_posix()
        if any(part in rel for part in allowed):
            continue
        assert "personal_unofficial" not in path.read_text(), rel


def test_personal_channels_are_tenant_scoped(enabled, personal, client_b, owner_b):
    assert client_b.get("/api/personal-channels/").json()["count"] == 0
    for url in ("", "login/start/", "emergency-stop/", "resume/", "limits/"):
        method = client_b.get if url in ("", "limits/") else client_b.post
        assert method(f"/api/personal-channels/{personal.pk}/{url}").status_code == 404


def test_oa_channels_are_not_listed(enabled, client_a, tenant_a, personal):
    ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    body = client_a.get("/api/personal-channels/").json()
    assert [row["id"] for row in body["results"]] == [personal.pk]


# --- QR login ---------------------------------------------------------------------------


def test_login_needs_risk_acknowledgement_first(enabled, client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    response = client_a.post(f"/api/personal-channels/{channel.pk}/login/start/")
    assert response.status_code == 400
    assert "xác nhận rủi ro" in response.json()["detail"]


def test_only_owner_can_login(enabled, personal, admin_a, viewer_a):
    for user in (admin_a, viewer_a):
        response = auth_client(user).post(f"/api/personal-channels/{personal.pk}/login/start/")
        assert response.status_code == 403


def test_qr_login_flow(enabled, client_a, personal):
    response = client_a.post(f"/api/personal-channels/{personal.pk}/login/start/")
    assert response.status_code == 201, response.json()
    login_id = response.json()["login_id"]
    assert response.json()["qr_image"].startswith("data:image/png;base64,")
    status_url = f"/api/personal-channels/{personal.pk}/login/status/"
    assert client_a.post(status_url, {"login_id": login_id}, "json").json()["state"] == "scanned"
    body = client_a.post(status_url, {"login_id": login_id}, "json").json()
    assert body["state"] == "confirmed" and body["account_id"] == "fake-zalo-account-1"
    assert "session" not in body
    personal.refresh_from_db()
    assert personal.status == Channel.Status.ACTIVE
    assert personal.session_data == f"fake-session-{login_id}"
    assert personal.mode == "assist"  # still the safe default; owner switches to auto
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_data FROM zalo_channels_channel WHERE id=%s", [personal.pk])
        assert "fake-session" not in cursor.fetchone()[0]  # encrypted at rest
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"channel.personal_login_started", "channel.personal_logged_in"} <= actions
    for log in AuditLog.objects.all():
        assert "fake-session" not in json.dumps(log.changes)
    detail = client_a.get(f"/api/personal-channels/{personal.pk}/").json()
    assert detail["logged_in"] and detail["zalo_account_id"] == "fake-zalo-account-1"
    assert "session_data" not in detail


def test_login_rejects_unknown_or_expired_login_id(enabled, client_a, personal):
    status_url = f"/api/personal-channels/{personal.pk}/login/status/"
    assert client_a.post(status_url, {"login_id": "nope"}, "json").status_code == 400
    login_id = client_a.post(f"/api/personal-channels/{personal.pk}/login/start/").json()[
        "login_id"
    ]
    FakePersonalAdapter.expire(login_id)
    assert client_a.post(status_url, {"login_id": login_id}, "json").json()["state"] == "expired"
    assert AuditLog.objects.filter(action="channel.personal_login_failed").exists()
    personal.refresh_from_db()
    assert "pending_login" not in personal.config


def test_login_times_out_on_our_side(enabled, client_a, personal):
    login_id = client_a.post(f"/api/personal-channels/{personal.pk}/login/start/").json()[
        "login_id"
    ]
    personal.refresh_from_db()
    personal.config["pending_login"]["expires_at"] = (
        timezone.now() - timedelta(seconds=1)
    ).isoformat()
    personal.save()
    status_url = f"/api/personal-channels/{personal.pk}/login/status/"
    assert client_a.post(status_url, {"login_id": login_id}, "json").json()["state"] == "expired"


def test_same_zalo_account_cannot_be_connected_twice(enabled, client_a, personal, tenant_b):
    other = ChannelFactory(
        tenant=tenant_b,
        channel_type="zalo_personal",
        config={"zalo_account_id": "fake-zalo-account-1"},
    )
    assert other.pk
    login_id = client_a.post(f"/api/personal-channels/{personal.pk}/login/start/").json()[
        "login_id"
    ]
    status_url = f"/api/personal-channels/{personal.pk}/login/status/"
    client_a.post(status_url, {"login_id": login_id}, "json")
    response = client_a.post(status_url, {"login_id": login_id}, "json")
    assert response.status_code == 400
    personal.refresh_from_db()
    assert personal.session_data == "" and personal.status != Channel.Status.ACTIVE


def test_logout_wipes_session(enabled, client_a, live):
    response = client_a.post(f"/api/personal-channels/{live.pk}/logout/")
    assert response.status_code == 200
    live.refresh_from_db()
    assert live.session_data == "" and live.status == Channel.Status.DISCONNECTED


# --- Send guard -------------------------------------------------------------------------


def test_reply_to_someone_who_wrote_first_is_sent(enabled, conversation):
    result = dispatch_reply(conversation, "Dạ em chào anh")
    assert result.sent
    assert FakePersonalAdapter.sent == [(conversation.channel_id, "zuid-khach-1", "Dạ em chào anh")]
    conversation.channel.refresh_from_db()
    assert "last_sent_at" in conversation.channel.config


def test_never_starts_a_conversation(enabled, live):
    contact = ContactFactory(channel=live, has_messaged_first=False)
    with pytest.raises(ReplyNotAllowed):
        dispatch_reply(ConversationFactory(contact=contact), "Chào bạn")
    # Flag set but no inbound message stored: still refused by the guard.
    contact2 = ContactFactory(channel=live, has_messaged_first=True)
    with pytest.raises(SendFailed, match="no_inbound"):
        dispatch_reply(ConversationFactory(contact=contact2), "Chào bạn")
    assert FakePersonalAdapter.sent == []


def test_reply_window(enabled, live):
    contact = ContactFactory(channel=live)
    conv = ConversationFactory(contact=contact)
    inbound(conv, minutes_ago=25 * 60)
    with pytest.raises(SendFailed, match="reply_window_passed"):
        dispatch_reply(conv, "Dạ")


def test_not_faster_than_a_person(enabled, live):
    conv = ConversationFactory(contact=ContactFactory(channel=live))
    inbound(conv, minutes_ago=0)
    with pytest.raises(SendFailed) as exc:
        dispatch_reply(conv, "Dạ")
    assert "too_fast" in str(exc.value) and exc.value.retry_after >= 1


@pytest.mark.parametrize(
    ("setting", "reason"),
    [
        ({"ZALO_PERSONAL_MAX_PER_MINUTE": 1}, "rate_minute"),
        ({"ZALO_PERSONAL_MAX_PER_HOUR": 1}, "rate_hour"),
        ({"ZALO_PERSONAL_MAX_PER_DAY": 1}, "rate_day"),
        ({"ZALO_PERSONAL_MAX_PER_CONTACT_PER_HOUR": 1}, "rate_contact"),
    ],
)
def test_caps(enabled, conversation, setting, reason):
    app_sent(conversation, minutes_ago=0.5 if reason == "rate_minute" else 5)
    with override_settings(ZALO_PERSONAL_MIN_GAP_SECONDS=0, **setting):
        decision = check_send(conversation.channel, conversation.contact)
    assert not decision.allowed and decision.reason == reason
    assert decision.retry_after_seconds


def test_min_gap_uses_lock_marker(enabled, conversation):
    channel = conversation.channel
    channel.config = {**channel.config, "last_sent_at": timezone.now().isoformat()}
    channel.save()
    decision = check_send(channel, conversation.contact)
    assert decision.reason == "min_gap"


def test_owner_typed_messages_do_not_use_the_ai_quota(enabled, conversation):
    MessageFactory(conversation=conversation, direction="outbound")  # echo, not "out-"
    with override_settings(ZALO_PERSONAL_MAX_PER_DAY=1, ZALO_PERSONAL_MIN_GAP_SECONDS=0):
        assert check_send(conversation.channel, conversation.contact).allowed


def test_channel_config_can_only_tighten_limits(enabled, conversation):
    channel = conversation.channel
    app_sent(conversation, minutes_ago=5)
    channel.config = {**channel.config, "limits": {"per_day": 1000}}
    with override_settings(ZALO_PERSONAL_MAX_PER_DAY=1, ZALO_PERSONAL_MIN_GAP_SECONDS=0):
        assert check_send(channel, conversation.contact).reason == "rate_day"
    channel.config["limits"] = {"per_day": 1, "min_gap_seconds": 0}
    with override_settings(ZALO_PERSONAL_MIN_GAP_SECONDS=0):
        assert check_send(channel, conversation.contact).reason == "rate_day"


def test_time_window(enabled, conversation):
    with override_settings(ZALO_PERSONAL_SEND_WINDOW="07:30-21:30"):
        night = timezone.localtime().replace(hour=23, minute=0)
        decision = check_send(conversation.channel, conversation.contact, now=night)
    assert decision.reason == "outside_hours" and decision.retry_after_seconds > 0
    start, end = parse_window("20:00-02:00")
    assert in_window(night, start, end)
    assert not in_window(night.replace(hour=12), start, end)
    with pytest.raises(Exception, match="SEND_WINDOW"):
        parse_window("bad")


def test_global_kill_switch(enabled, conversation):
    with override_settings(ZALO_PERSONAL_EMERGENCY_STOP=True):
        with pytest.raises(SendFailed, match="global_stop"):
            dispatch_reply(conversation, "Dạ")


def test_guard_needs_current_risk_acknowledgement(enabled, conversation):
    ChannelRiskAcknowledgement.objects.update(revoked_at=timezone.now())
    assert check_send(conversation.channel, conversation.contact).reason in {
        "risk_not_acknowledged"
    }


def test_contact_of_another_channel_is_refused(enabled, conversation, tenant_b):
    other = ContactFactory(channel=ChannelFactory(tenant=tenant_b, channel_type="zalo_personal"))
    assert check_send(conversation.channel, other).reason == "unknown_contact"


def test_human_delay_is_bounded():
    import random

    rng = random.Random(1)
    short = human_delay_seconds("Dạ", rng)
    long = human_delay_seconds("x" * 5000, rng)
    assert 3 <= short <= 10 and long == 45


# --- Emergency stop / resume / auto-stop -------------------------------------------------


def test_emergency_stop_and_resume(enabled, conversation, admin_a, client_a):
    channel = conversation.channel
    response = auth_client(admin_a).post(
        f"/api/personal-channels/{channel.pk}/emergency-stop/", {"reason": "kiểm tra"}, "json"
    )
    assert response.status_code == 200
    channel.refresh_from_db()
    assert channel.status == Channel.Status.PAUSED and channel.mode == "assist"
    assert AuditLog.objects.filter(action="channel.personal_emergency_stop").exists()
    # assist mode: the draft goes to the owner's inbox, nothing is sent
    assert dispatch_reply(conversation, "Dạ").pending is not None
    assert FakePersonalAdapter.sent == []
    assert (
        auth_client(admin_a).post(f"/api/personal-channels/{channel.pk}/resume/").status_code == 403
    )
    response = client_a.post(f"/api/personal-channels/{channel.pk}/resume/")
    assert response.status_code == 200, response.json()
    channel.refresh_from_db()
    assert channel.status == Channel.Status.ACTIVE and channel.mode == "auto"
    assert "emergency_stop" not in channel.config


def test_restriction_sign_stops_channel_and_tells_owner(enabled, conversation, owner_a, client_a):
    owner_a.email = "chu@example.com"
    owner_a.save()
    received = []
    control.personal_channel_auto_stopped.connect(
        lambda **kw: received.append(kw["kind"]), weak=False, dispatch_uid="t"
    )
    FakePersonalAdapter.fail_next("blocked")
    with pytest.raises(SendFailed, match="restricted:blocked"):
        dispatch_reply(conversation, "Dạ")
    control.personal_channel_auto_stopped.disconnect(dispatch_uid="t")
    channel = Channel.objects.get(pk=conversation.channel_id)  # persisted despite the failure
    assert channel.status == Channel.Status.RESTRICTED and channel.mode == "assist"
    assert channel.config["auto_stopped"]["kind"] == "blocked"
    assert received == ["blocked"]
    assert mail.outbox and mail.outbox[0].to == ["chu@example.com"]
    assert AuditLog.objects.filter(action="channel.personal_auto_stopped").exists()
    resume_url = f"/api/personal-channels/{channel.pk}/resume/"
    assert client_a.post(resume_url).status_code == 400
    response = client_a.post(resume_url, {"restriction_reviewed": True}, "json")
    assert response.status_code == 200
    channel.refresh_from_db()
    assert channel.status == Channel.Status.ACTIVE and channel.mode == "auto"


def test_consecutive_failures_auto_stop(enabled, conversation):
    FakePersonalAdapter.fail_next("error", "error", "error")
    for _ in range(2):
        with pytest.raises(SendFailed):
            dispatch_reply(conversation, "Dạ")
    assert Channel.objects.get(pk=conversation.channel_id).status == Channel.Status.ACTIVE
    with pytest.raises(SendFailed):
        dispatch_reply(conversation, "Dạ")
    channel = Channel.objects.get(pk=conversation.channel_id)
    assert channel.config["auto_stopped"]["kind"] == "too_many_failures"


def test_success_resets_failure_counter(enabled, conversation):
    FakePersonalAdapter.fail_next("error")
    with pytest.raises(SendFailed):
        dispatch_reply(conversation, "Dạ")
    assert dispatch_reply(conversation, "Dạ").sent
    assert Channel.objects.get(pk=conversation.channel_id).config["consecutive_failures"] == 0


def test_resume_requirements(enabled, client_a, personal):
    url = f"/api/personal-channels/{personal.pk}/resume/"
    assert "đăng nhập" in client_a.post(url).json()["detail"].lower()
    personal.session_data = "s"
    personal.save()
    ChannelRiskAcknowledgement.objects.update(revoked_at=timezone.now())
    assert "xác nhận rủi ro" in client_a.post(url).json()["detail"]


def test_limits_endpoint(enabled, client_a, conversation, viewer_a):
    app_sent(conversation, minutes_ago=5)
    body = (
        auth_client(viewer_a)
        .get(f"/api/personal-channels/{conversation.channel_id}/limits/")
        .json()
    )
    assert body["can_send"] and body["usage"]["day"] == 1
    assert body["limits"]["send_window"] == "00:00-00:00"


# --- Sidecar events (webhook) ------------------------------------------------------------


def post_event(client, channel, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    return client.generic(
        "POST",
        f"/webhooks/zalo-personal/{channel.webhook_key}/",
        body,
        content_type="application/json",
        HTTP_X_ZP_TIMESTAMP=ts,
        HTTP_X_ZP_SIGNATURE=sign(secret, ts, body),
    )


def message_event(msg_id="m1", thread_id="zuid-new", text="Chào shop", **extra):
    return {
        "type": "message",
        "event_id": f"k:{msg_id}",
        "account_id": "fake-zalo-account-1",
        "message": {
            "thread_id": thread_id,
            "thread_type": "user",
            "from_id": thread_id,
            "from_name": "Khách Mới",
            "msg_id": msg_id,
            "text": text,
            "ts": int(time.time() * 1000),
            "is_self": False,
            **extra,
        },
    }


def test_inbound_message_from_sidecar(enabled, client, live, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert post_event(client, live, message_event()).json()["status"] == "ok"
    message = Message.objects.get(zalo_message_id="m1")
    assert message.direction == "inbound" and message.content == "Chào shop"
    contact = message.conversation.contact
    assert contact.has_messaged_first and contact.display_name == "Khách Mới"
    assert contact.tenant_id == live.tenant_id and not contact.has_consent
    # duplicate delivery is ignored
    assert post_event(client, live, message_event()).json()["status"] == "duplicate"


def test_owner_own_message_is_stored_outbound(
    enabled, client, live, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        post_event(client, live, message_event("m2", is_self=True))
    message = Message.objects.get(zalo_message_id="m2")
    assert message.direction == "outbound"
    assert not message.conversation.contact.has_messaged_first


def test_group_messages_are_ignored(enabled, client, live, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        post_event(client, live, message_event("g1", thread_type="group"))
    assert not Message.objects.filter(zalo_message_id="g1").exists()


def test_webhook_rejects_bad_signature_and_unknown_channel(enabled, client, live):
    assert post_event(client, live, message_event(), secret="wrong").status_code == 401
    other = ChannelFactory(channel_type="zalo_oa")
    body = json.dumps(message_event()).encode()
    ts = str(int(time.time()))
    response = client.generic(
        "POST",
        f"/webhooks/zalo-personal/{other.webhook_key}/",
        body,
        content_type="application/json",
        HTTP_X_ZP_TIMESTAMP=ts,
        HTTP_X_ZP_SIGNATURE=sign(SECRET, ts, body),
    )
    assert response.status_code == 404
    old = str(int(time.time()) - 3600)
    response = client.generic(
        "POST",
        f"/webhooks/zalo-personal/{live.webhook_key}/",
        body,
        content_type="application/json",
        HTTP_X_ZP_TIMESTAMP=old,
        HTTP_X_ZP_SIGNATURE=sign(SECRET, old, body),
    )
    assert response.status_code == 401


def test_restriction_event_stops_channel(enabled, client, live):
    payload = {
        "type": "restriction",
        "account_id": "fake-zalo-account-1",
        "restriction": {"kind": "kicked", "detail": "listener closed (3003)"},
    }
    assert post_event(client, live, payload).json()["status"] == "stopped"
    live.refresh_from_db()
    assert live.status == Channel.Status.RESTRICTED and live.config["auto_stopped"]["kind"] == (
        "kicked"
    )


def test_event_from_another_account_stops_channel(enabled, client, live):
    payload = message_event()
    payload["account_id"] = "someone-else"
    assert post_event(client, live, payload).json()["status"] == "stopped"
    live.refresh_from_db()
    assert live.config["auto_stopped"]["kind"] == "account_changed"


def test_session_update_event(enabled, client, live):
    payload = {"type": "session_updated", "account_id": "fake-zalo-account-1", "session": "new"}
    assert post_event(client, live, payload).status_code == 200
    live.refresh_from_db()
    assert live.session_data == "new"
    assert post_event(client, live, {"type": "other"}).json()["status"] == "ignored"


# --- Sidecar adapter (HTTP contract) -----------------------------------------------------


class FakeSidecar:
    def __init__(self):
        self.calls = []
        self.sessions = set()
        self.send_response = {"ok": True, "message_id": "77"}

    def handler(self, request: httpx.Request):
        body = request.content
        ts = request.headers["x-zp-timestamp"]
        assert request.headers["x-zp-signature"] == sign(SECRET, ts, body)
        path = request.url.path
        payload = json.loads(body or b"{}")
        self.calls.append((request.method, path))
        key = path.split("/")[3] if path.startswith("/v1/sessions/") else ""
        if path.endswith("/login"):
            return httpx.Response(
                200,
                json={
                    "login_id": "11111111-1111-1111-1111-111111111111",
                    "qr_image": "data:image/png;base64,AAA",
                    "expires_at": "2030-01-01T00:00:00.000Z",
                },
            )
        if path.endswith("/status"):
            return httpx.Response(
                200,
                json={
                    "state": "confirmed",
                    "account_id": "a1",
                    "display_name": "C",
                    "session": "s",
                },
            )
        if path.endswith("/restore"):
            if payload.get("session") != "good":
                return httpx.Response(200, json={"ok": False, "restriction": "session_expired"})
            self.sessions.add(key)
            return httpx.Response(200, json={"ok": True, "account_id": "a1"})
        if path.endswith("/send"):
            if key not in self.sessions:
                return httpx.Response(409, json={"ok": False, "error": "no_session"})
            return httpx.Response(200, json=self.send_response)
        if request.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def sidecar():
    fake = FakeSidecar()
    with use_transport(httpx.MockTransport(fake.handler)):
        yield fake


def test_sidecar_adapter_login(sidecar, live):
    adapter = SidecarAdapter()
    challenge = adapter.start_login(live)
    assert challenge.login_id and challenge.expires_at.year == 2030
    status = adapter.login_status(live, challenge.login_id)
    assert status.state == LoginState.CONFIRMED and status.session == "s"
    adapter.logout(live)
    assert sidecar.calls[0] == ("POST", f"/v1/sessions/{live.webhook_key}/login")


def test_sidecar_adapter_restores_session_then_sends(sidecar, live):
    live.session_data = "good"
    live.config = {"zalo_account_id": "a1"}
    result = SidecarAdapter().send_text(live, "u1", "Dạ")
    assert result.ok and result.message_id == "77"
    assert [c[1].rsplit("/", 1)[1] for c in sidecar.calls] == ["send", "restore", "send"]


def test_sidecar_adapter_restriction_signals(sidecar, live):
    live.session_data = "bad"
    with pytest.raises(RestrictionDetected, match="session_expired"):
        SidecarAdapter().send_text(live, "u1", "Dạ")
    live.session_data = "good"
    live.config = {"zalo_account_id": "other-account"}
    with pytest.raises(RestrictionDetected, match="account_changed"):
        SidecarAdapter().send_text(live, "u1", "Dạ")
    live.config = {"zalo_account_id": "a1"}
    sidecar.send_response = {"ok": False, "error": "bị chặn", "restriction": "blocked"}
    with pytest.raises(RestrictionDetected, match="blocked"):
        SidecarAdapter().send_text(live, "u1", "Dạ")
    sidecar.send_response = {"ok": False, "error": "timeout"}
    assert SidecarAdapter().send_text(live, "u1", "Dạ").error == "timeout"


def test_sidecar_adapter_needs_configuration(live):
    with override_settings(ZALO_PERSONAL_SIDECAR_URL=""), pytest.raises(SidecarError):
        SidecarAdapter().start_login(live)


def test_sidecar_unreachable_counts_as_failure(enabled, conversation):
    def down(request):
        raise httpx.ConnectError("down")

    with (
        override_settings(ZALO_CHANNEL_BACKEND="real"),
        use_transport(httpx.MockTransport(down)),
    ):
        with pytest.raises(SendFailed, match="sidecar unreachable"):
            dispatch_reply(conversation, "Dạ")
    assert Channel.objects.get(pk=conversation.channel_id).config["consecutive_failures"] == 1


def test_sidecar_error_is_shown_nicely(enabled, client_a, personal):
    with (
        override_settings(ZALO_CHANNEL_BACKEND="real", ZALO_PERSONAL_SIDECAR_SECRET=""),
    ):
        response = client_a.post(f"/api/personal-channels/{personal.pk}/login/start/")
    assert response.status_code == 400 and "sidecar" in response.json()["detail"]
