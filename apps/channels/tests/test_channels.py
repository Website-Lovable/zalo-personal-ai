import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.test import override_settings

from apps.channels.models import Channel, ChannelMode
from apps.channels.providers import get_provider, provider_class_for
from apps.channels.providers.mock import MockChannelProvider
from apps.channels.providers.personal_unofficial import PersonalUnofficialProvider
from apps.channels.providers.zalo_bot import ZaloBotProvider
from apps.channels.providers.zalo_oa import ZaloOAProvider
from common.exceptions import FeatureDisabledError, NotImplementedInPhase
from common.fields import decrypt_value, encrypt_value
from common.models import AuditLog
from tests.factories import ChannelFactory

pytestmark = pytest.mark.django_db

ACK = {"full_name": "Nguyễn Văn A", "statement_version": "v1", "accepted": True}


def test_create_channel_types(client_a):
    for channel_type in ["zalo_oa", "zalo_bot", "zalo_personal"]:
        response = client_a.post(
            "/api/channels/", {"channel_type": channel_type, "name": channel_type}, format="json"
        )
        assert response.status_code == 201, response.json()
    assert AuditLog.objects.filter(action="channel.created").count() == 3


def test_personal_channel_defaults_to_assist(client_a):
    response = client_a.post(
        "/api/channels/", {"channel_type": "zalo_personal", "name": "P"}, format="json"
    )
    assert response.json()["mode"] == "assist"
    response = client_a.post(
        "/api/channels/",
        {"channel_type": "zalo_personal", "name": "P", "mode": "auto"},
        format="json",
    )
    assert response.status_code == 400


def test_personal_auto_requires_risk_ack_then_allowed(client_a, owner_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    assert channel.mode == ChannelMode.ASSIST
    url = f"/api/channels/{channel.pk}/"
    assert client_a.patch(url, {"mode": "auto"}, format="json").status_code == 400

    statement = client_a.get(url + "risk-statement/").json()
    assert statement["statement_version"] == "v1"
    assert "Điều khoản" in statement["statement_text"]

    response = client_a.post(url + "acknowledge-risk/", ACK, format="json")
    assert response.status_code == 201
    assert response.json()["risk_acknowledged"] is True
    log = AuditLog.objects.get(action="channel.risk_acknowledged")
    assert log.user == owner_a and log.changes["statement_version"] == "v1"

    assert client_a.patch(url, {"mode": "auto"}, format="json").status_code == 200
    channel.refresh_from_db()
    assert channel.mode == ChannelMode.AUTO


@pytest.mark.parametrize(
    "payload",
    [
        {**ACK, "accepted": False},
        {**ACK, "statement_version": "v0"},
        {"accepted": True},
    ],
)
def test_risk_ack_validation(client_a, tenant_a, payload):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    response = client_a.post(
        f"/api/channels/{channel.pk}/acknowledge-risk/", payload, format="json"
    )
    assert response.status_code == 400
    assert not channel.risk_acknowledged


def test_risk_ack_only_for_personal(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    response = client_a.post(f"/api/channels/{channel.pk}/acknowledge-risk/", ACK, format="json")
    assert response.status_code == 400


def test_model_guards_personal_auto(tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    channel.mode = ChannelMode.AUTO
    with pytest.raises(ValidationError):
        channel.full_clean()
    with pytest.raises(ValidationError):
        channel.save()
    assert not Channel(channel_type="zalo_personal").risk_acknowledged


def test_channel_type_cannot_change(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a)
    response = client_a.patch(
        f"/api/channels/{channel.pk}/", {"channel_type": "zalo_bot"}, format="json"
    )
    assert response.status_code == 400


def test_secrets_not_allowed_in_config(client_a):
    response = client_a.post(
        "/api/channels/",
        {"channel_type": "zalo_oa", "name": "x", "config": {"app_secret": "abc"}},
        format="json",
    )
    assert response.status_code == 400


def test_tokens_encrypted_at_rest_and_never_returned(client_a, tenant_a):
    response = client_a.post(
        "/api/channels/",
        {"channel_type": "zalo_oa", "name": "x", "access_token": "tok-123"},
        format="json",
    )
    body = response.json()
    assert "access_token" not in body and body["has_access_token"] is True
    channel = Channel.objects.get(pk=body["id"])
    assert channel.access_token == "tok-123"
    with connection.cursor() as cursor:
        cursor.execute("SELECT access_token FROM zalo_channels_channel WHERE id = %s", [channel.pk])
        raw = cursor.fetchone()[0]
    assert raw.startswith("enc$") and "tok-123" not in raw

    client_a.patch(f"/api/channels/{channel.pk}/", {"refresh_token": "r-1"}, format="json")
    log = AuditLog.objects.get(action="channel.credentials_changed")
    assert log.changes == {"fields": ["refresh_token"]}
    updated = AuditLog.objects.get(action="channel.updated")
    assert updated.changes["refresh_token"] == "***"


def test_encryption_helpers():
    assert decrypt_value(encrypt_value("hello")) == "hello"
    assert decrypt_value("plain") == "plain"


@override_settings(ENCRYPTION_KEY="", DEBUG=False)
def test_encryption_requires_key_in_production():
    from common.fields import get_fernet

    with pytest.raises(ImproperlyConfigured):
        get_fernet()


@override_settings(ENCRYPTION_KEY="", DEBUG=True)
def test_encryption_dev_fallback():
    assert decrypt_value(encrypt_value("x")) == "x"


def test_decrypt_with_wrong_key_fails():
    token = encrypt_value("secret")
    with override_settings(ENCRYPTION_KEY="Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmE="):
        with pytest.raises(ImproperlyConfigured):
            decrypt_value(token)


def test_provider_registry(tenant_a):
    assert provider_class_for("zalo_oa") is ZaloOAProvider
    assert provider_class_for("zalo_bot") is ZaloBotProvider
    with pytest.raises(FeatureDisabledError):
        provider_class_for("zalo_personal")
    with pytest.raises(ValueError):
        provider_class_for("telegram")
    channel = ChannelFactory(tenant=tenant_a)
    assert isinstance(get_provider(channel), MockChannelProvider)
    personal = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    with pytest.raises(FeatureDisabledError):
        get_provider(personal)


@override_settings(ZALO_PERSONAL_UNOFFICIAL_ENABLED=True, ZALO_CHANNEL_BACKEND="real")
def test_personal_provider_is_still_a_phase_2b_stub(tenant_a):
    assert provider_class_for("zalo_personal") is PersonalUnofficialProvider
    assert isinstance(
        get_provider(ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")), ZaloOAProvider
    )
    for channel_type in ["zalo_personal"]:
        provider = get_provider(ChannelFactory(tenant=tenant_a, channel_type=channel_type))
        for call in (
            lambda p=provider: p.verify_webhook({}, b""),
            lambda p=provider: p.parse_webhook({}),
            lambda p=provider: p.send_text("u", "hi"),
        ):
            with pytest.raises(NotImplementedInPhase):
                call()


def test_mock_provider_roundtrip(tenant_a):
    channel = ChannelFactory(tenant=tenant_a, config={"webhook_secret": "s"})
    provider = MockChannelProvider(channel)
    assert provider.verify_webhook({"X-Mock-Secret": "s"}, b"")
    assert not provider.verify_webhook({"X-Mock-Secret": "x"}, b"")
    messages = provider.parse_webhook({"messages": [{"sender_id": 1, "text": "hi"}]})
    assert messages[0].sender_id == "1" and messages[0].text == "hi"
    assert provider.send_text("1", "chào").ok
    assert MockChannelProvider.sent == [(channel.pk, "1", "chào")]


def test_filter_channels(client_a, tenant_a):
    ChannelFactory(tenant=tenant_a, channel_type="zalo_oa")
    ChannelFactory(tenant=tenant_a, channel_type="zalo_bot")
    response = client_a.get("/api/channels/?channel_type=zalo_bot")
    assert response.json()["count"] == 1


def test_delete_channel_is_audited(client_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a)
    assert client_a.delete(f"/api/channels/{channel.pk}/").status_code == 204
    assert AuditLog.objects.filter(action="channel.deleted", object_id=str(channel.pk)).exists()
