import json

import pytest
from django.test import override_settings

from apps.integrations.aiva.client import SANDBOX_MOCK
from apps.integrations.aiva.signing import sign_request_headers
from tests.factories import ChannelFactory, ContactFactory, ConversationFactory

SERVICE_TOKEN = "svc-token-test"
INBOUND_SECRET = "inbound-secret-test"
OUTBOUND_SECRET = "outbound-secret-test"

SANDBOX = {
    "AIVA_INTEGRATION_MODE": "sandbox",
    "AIVA_CLIENT": "mock",
    "AIVA_SERVICE_TOKEN": SERVICE_TOKEN,
    "AIVA_INBOUND_SECRET": INBOUND_SECRET,
    "AIVA_WEBHOOK_SECRET": OUTBOUND_SECRET,
    "AIVA_BASE_URL": "https://aiva.test",
}


@pytest.fixture
def sandbox():
    SANDBOX_MOCK.received.clear()
    with override_settings(**SANDBOX):
        yield SANDBOX_MOCK
    SANDBOX_MOCK.received.clear()


@pytest.fixture
def demo_channel(tenant_a):
    return ChannelFactory(tenant=tenant_a, data_origin="demo", status="active")


@pytest.fixture
def demo_contact(demo_channel):
    contact = ContactFactory(
        channel=demo_channel,
        data_origin="demo",
        crs_customer_id="CRS-1",
        owner_staff_ref="staff-01",
        segment="b2b",
    )
    contact.set_consent("granted", "crs")
    contact.save()
    return contact


@pytest.fixture
def demo_conversation(demo_contact):
    return ConversationFactory(contact=demo_contact, data_origin="demo")


class AIVACaller:
    """Calls /api/integration/v1/ the way AIVA would (token + HMAC + tenant header)."""

    def __init__(self, client):
        self.client = client

    def __call__(
        self,
        method,
        path,
        body=None,
        *,
        tenant_ref="crs-a",
        key="key-1",
        token=SERVICE_TOKEN,
        secret=INBOUND_SECRET,
        now=None,
        sign_path=None,
    ):
        full = f"/api/integration/v1/{path}"
        raw = json.dumps(body).encode() if body is not None else b""
        headers = sign_request_headers(secret, method, sign_path or full, tenant_ref, raw, now=now)
        extra = {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
            "HTTP_X_TENANT_REF": tenant_ref,
            "HTTP_X_SIGNATURE": headers["X-Signature"],
            "HTTP_X_TIMESTAMP": headers["X-Timestamp"],
        }
        if key:
            extra["HTTP_IDEMPOTENCY_KEY"] = key
        if method == "GET":
            return self.client.get(full, **extra)
        return self.client.generic(method, full, raw, content_type="application/json", **extra)


@pytest.fixture
def aiva(client):
    return AIVACaller(client)
