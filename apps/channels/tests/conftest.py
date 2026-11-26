import pytest
from django.test import override_settings

from apps.channels.providers.http import use_transport
from apps.channels.testing.fake_zalo import FakeZalo
from tests.factories import ChannelFactory


@pytest.fixture
def fake_zalo():
    """Real providers + the in-process fake Zalo APIs (no network)."""
    fake = FakeZalo()
    with override_settings(ZALO_CHANNEL_BACKEND="real"), use_transport(fake.transport):
        yield fake


@pytest.fixture
def oa_channel(tenant_a, fake_zalo):
    from datetime import timedelta

    from django.utils import timezone

    access, refresh = fake_zalo.issue_tokens()
    return ChannelFactory(
        tenant=tenant_a,
        channel_type="zalo_oa",
        status="active",
        zalo_oa_id=fake_zalo.oa_id,
        access_token=access,
        refresh_token=refresh,
        token_expires_at=timezone.now() + timedelta(hours=20),
    )


@pytest.fixture
def bot_channel(tenant_a, fake_zalo):
    return ChannelFactory(
        tenant=tenant_a,
        channel_type="zalo_bot",
        status="active",
        access_token="111:valid-bot-token",
        webhook_secret="bot-secret-token-123",
        config={"delivery": "webhook"},
    )
