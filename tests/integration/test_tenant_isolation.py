"""Acceptance criterion 3: tenant A can never read or change tenant B's data."""

import pytest

from apps.contacts.models import Contact
from tests.factories import (
    ApprovalFactory,
    ChannelFactory,
    ContactFactory,
    ContactGroupFactory,
    ConversationFactory,
    LongTermMemoryFactory,
    MessageFactory,
    PermissionRuleFactory,
)

pytestmark = pytest.mark.django_db

LIST_ENDPOINTS = [
    "channels",
    "contacts",
    "contact-groups",
    "conversations",
    "permissions/rules",
    "approvals",
    "owner-sends",
]


@pytest.fixture
def data_b(tenant_b, owner_b):
    channel = ChannelFactory(tenant=tenant_b)
    contact = ContactFactory(channel=channel)
    conversation = ConversationFactory(contact=contact)
    message = MessageFactory(conversation=conversation)
    return {
        "channels": channel,
        "contacts": contact,
        "contact-groups": ContactGroupFactory(tenant=tenant_b),
        "conversations": conversation,
        "permissions/rules": PermissionRuleFactory(tenant=tenant_b),
        "approvals": ApprovalFactory(message=message),
        "memory": LongTermMemoryFactory(contact=contact),
        "message": message,
    }


@pytest.mark.parametrize("endpoint", LIST_ENDPOINTS)
def test_list_never_contains_other_tenant(client_a, data_b, endpoint):
    response = client_a.get(f"/api/{endpoint}/")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_users_list_only_own_tenant(client_a, owner_a, owner_b):
    usernames = {u["username"] for u in client_a.get("/api/users/").json()["results"]}
    assert usernames == {owner_a.username}
    assert client_a.get(f"/api/users/{owner_b.pk}/").status_code == 404
    assert client_a.delete(f"/api/users/{owner_b.pk}/").status_code == 404


@pytest.mark.parametrize(
    "endpoint",
    ["channels", "contacts", "contact-groups", "conversations", "permissions/rules", "approvals"],
)
def test_detail_of_other_tenant_is_404(client_a, data_b, endpoint):
    obj = data_b[endpoint]
    assert client_a.get(f"/api/{endpoint}/{obj.pk}/").status_code == 404


@pytest.mark.parametrize(
    "endpoint", ["channels", "contacts", "contact-groups", "conversations", "permissions/rules"]
)
def test_cannot_update_or_delete_other_tenant(client_a, data_b, endpoint):
    obj = data_b[endpoint]
    assert (
        client_a.patch(f"/api/{endpoint}/{obj.pk}/", {"name": "x"}, format="json").status_code
        == 404
    )
    assert client_a.delete(f"/api/{endpoint}/{obj.pk}/").status_code == 404
    assert type(obj).objects.filter(pk=obj.pk).exists()


def test_nested_resources_of_other_tenant_are_hidden(client_a, data_b):
    assert client_a.get(f"/api/contacts/{data_b['contacts'].pk}/memory/").status_code == 404
    conv = data_b["conversations"].pk
    assert client_a.get(f"/api/conversations/{conv}/messages/").status_code == 404
    assert client_a.post(f"/api/approvals/{data_b['approvals'].pk}/approve/").status_code == 404


def test_cannot_reference_other_tenant_objects_on_create(client_a, tenant_a, data_b):
    # Contact in tenant A pointing at tenant B's channel.
    response = client_a.post(
        "/api/contacts/",
        {
            "channel": data_b["channels"].pk,
            "zalo_user_id": "x",
            "display_name": "Spy",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "channel" in response.json()
    # Group in tenant A containing tenant B's contact.
    response = client_a.post(
        "/api/contact-groups/",
        {
            "name": "G",
            "contacts": [data_b["contacts"].pk],
        },
        format="json",
    )
    assert response.status_code == 400
    # Rule in tenant A bound to tenant B's group.
    response = client_a.post(
        "/api/permissions/rules/",
        {
            "name": "R",
            "action": "auto_reply",
            "contact_group": data_b["contact-groups"].pk,
        },
        format="json",
    )
    assert response.status_code == 400


def test_create_is_stamped_with_caller_tenant(client_a, tenant_a, tenant_b):
    response = client_a.post(
        "/api/channels/",
        {
            "channel_type": "zalo_oa",
            "name": "Mine",
            "tenant": tenant_b.pk,
        },
        format="json",
    )
    assert response.status_code == 201
    channel_id = response.json()["id"]
    from apps.channels.models import Channel

    assert Channel.objects.get(pk=channel_id).tenant == tenant_a


def test_queryset_helper_scopes_by_tenant(tenant_a, data_b):
    assert Contact.objects.for_tenant(tenant_a).count() == 0
    assert Contact.objects.for_tenant(None).count() == 0
    assert Contact.objects.for_tenant(data_b["contacts"].tenant).count() == 1


def test_dashboard_counts_only_own_tenant(client_a, tenant_a, data_b):
    ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    stats = client_a.get("/api/dashboard/stats/").json()
    assert stats["contacts"] == 1
    assert stats["channels"] == 1
    assert stats["pending_approvals"] == 0


def test_ai_profile_is_per_tenant(client_a, client_b):
    client_a.patch("/api/ai/profile/", {"occupation": "A job"}, format="json")
    assert client_b.get("/api/ai/profile/").json()["occupation"] == ""
