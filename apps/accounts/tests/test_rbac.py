"""RBAC: viewer = read-only, admin = write business data, owner = owner-only actions."""

import pytest

from conftest import auth_client
from tests.factories import ChannelFactory, ContactFactory

pytestmark = pytest.mark.django_db


def test_viewer_can_read_but_not_write(viewer_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a)
    client = auth_client(viewer_a)
    assert client.get("/api/channels/").status_code == 200
    assert client.get(f"/api/channels/{channel.pk}/").status_code == 200
    assert (
        client.post(
            "/api/channels/", {"channel_type": "zalo_oa", "name": "x"}, format="json"
        ).status_code
        == 403
    )
    assert (
        client.patch(f"/api/channels/{channel.pk}/", {"name": "y"}, format="json").status_code
        == 403
    )
    assert client.delete(f"/api/channels/{channel.pk}/").status_code == 403
    assert client.put("/api/ai/style/", {"formality_level": 3}, format="json").status_code == 403


def test_admin_can_write_business_data(admin_a, tenant_a):
    client = auth_client(admin_a)
    response = client.post(
        "/api/channels/", {"channel_type": "zalo_bot", "name": "Bot"}, format="json"
    )
    assert response.status_code == 201
    contact = ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    assert (
        client.patch(
            f"/api/contacts/{contact.pk}/", {"relationship_type": "friend"}, format="json"
        ).status_code
        == 200
    )


def test_only_owner_can_acknowledge_risk(admin_a, owner_a, tenant_a):
    channel = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    payload = {"full_name": "Nguyen Van A", "statement_version": "v1", "accepted": True}
    url = f"/api/channels/{channel.pk}/acknowledge-risk/"
    assert auth_client(admin_a).post(url, payload, format="json").status_code == 403
    assert auth_client(owner_a).post(url, payload, format="json").status_code == 201


def test_viewer_cannot_decide_approvals(viewer_a):
    client = auth_client(viewer_a)
    assert client.post("/api/approvals/1/approve/").status_code == 403
