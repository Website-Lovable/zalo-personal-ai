import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.channels.providers.mock import MockChannelProvider
from tests.factories import TenantFactory, UserFactory


@pytest.fixture(autouse=True)
def _reset_mock_provider():
    MockChannelProvider.reset()
    yield
    MockChannelProvider.reset()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Acceptance criterion 4: tests must never open a socket to the outside world."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host in ("127.0.0.1", "localhost", "::1") or isinstance(address, str):
            return real_connect(self, address)
        raise RuntimeError(f"Outbound network call blocked in tests: {address}")

    monkeypatch.setattr(socket.socket, "connect", guarded)


def auth_client(user) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def tenant_a(db):
    return TenantFactory(name="Tenant A", external_ref="crs-a")


@pytest.fixture
def tenant_b(db):
    return TenantFactory(name="Tenant B", external_ref="crs-b")


@pytest.fixture
def owner_a(tenant_a):
    return UserFactory(tenant=tenant_a, role="owner")


@pytest.fixture
def admin_a(tenant_a):
    return UserFactory(tenant=tenant_a, role="admin")


@pytest.fixture
def viewer_a(tenant_a):
    return UserFactory(tenant=tenant_a, role="viewer")


@pytest.fixture
def owner_b(tenant_b):
    return UserFactory(tenant=tenant_b, role="owner")


@pytest.fixture
def client_a(owner_a):
    return auth_client(owner_a)


@pytest.fixture
def client_b(owner_b):
    return auth_client(owner_b)
