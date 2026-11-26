import pytest
from django.core.exceptions import ValidationError

from apps.contacts.models import ConsentStatus, Contact
from apps.integrations.aiva.models import IntegrationOutbox
from common.models import AuditLog
from tests.factories import ChannelFactory, ContactFactory, LongTermMemoryFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def channel(tenant_a):
    return ChannelFactory(tenant=tenant_a)


def test_create_contact_defaults(client_a, channel):
    response = client_a.post(
        "/api/contacts/",
        {"channel": channel.pk, "zalo_user_id": "z1", "display_name": "Lan"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["reply_permission"] == "need_approval"
    assert body["consent_status"] == "unknown" and body["has_consent"] is False
    assert body["data_origin"] == "real"


def test_granting_consent_requires_source_and_sets_time(client_a, channel):
    payload = {
        "channel": channel.pk,
        "zalo_user_id": "z2",
        "display_name": "Minh",
        "consent_status": "granted",
    }
    response = client_a.post("/api/contacts/", payload, format="json")
    assert response.status_code == 400
    assert "consent_source" in response.json()

    payload["consent_source"] = "form"
    response = client_a.post("/api/contacts/", payload, format="json")
    assert response.status_code == 201
    assert response.json()["consent_at"] is not None


def test_consent_change_is_audited_and_not_sent_when_aiva_off(client_a, channel):
    contact = ContactFactory(channel=channel, crs_customer_id="CRS-1")
    response = client_a.patch(
        f"/api/contacts/{contact.pk}/",
        {"consent_status": "granted", "consent_source": "crs"},
        format="json",
    )
    assert response.status_code == 200
    contact.refresh_from_db()
    assert contact.has_consent and contact.consent_at is not None
    log = AuditLog.objects.get(action="contact.consent_changed")
    assert log.changes["from"] == "unknown" and log.changes["to"] == "granted"

    client_a.patch(f"/api/contacts/{contact.pk}/", {"consent_status": "withdrawn"}, format="json")
    assert AuditLog.objects.filter(action="contact.consent_changed").count() == 2
    assert IntegrationOutbox.objects.count() == 0  # AIVA_INTEGRATION_MODE=off


def test_contact_cannot_move_channel(client_a, channel, tenant_a):
    contact = ContactFactory(channel=channel)
    other = ChannelFactory(tenant=tenant_a)
    response = client_a.patch(f"/api/contacts/{contact.pk}/", {"channel": other.pk}, format="json")
    assert response.status_code == 400


def test_contact_memory_endpoint(client_a, channel):
    contact = ContactFactory(channel=channel)
    LongTermMemoryFactory(contact=contact, content="Thích trà")
    response = client_a.get(f"/api/contacts/{contact.pk}/memory/")
    assert response.status_code == 200
    assert response.json()["results"][0]["content"] == "Thích trà"


def test_reply_and_outreach_rules(tenant_a):
    oa = ChannelFactory(tenant=tenant_a)
    personal = ChannelFactory(tenant=tenant_a, channel_type="zalo_personal")
    contact = ContactFactory(channel=oa)
    assert contact.can_receive_reply()
    assert not contact.can_contact_proactively()  # no consent yet
    contact.set_consent(ConsentStatus.GRANTED, "crs")
    assert contact.can_contact_proactively()
    contact.set_consent(ConsentStatus.WITHDRAWN)
    assert contact.consent_source == "crs"
    assert not contact.can_receive_reply()
    assert not ContactFactory(channel=oa, reply_permission="do_not_reply").can_receive_reply()
    # Personal channel: only reply to people who wrote first; never proactive outreach.
    stranger = ContactFactory(channel=personal, has_messaged_first=False)
    assert not stranger.can_receive_reply()
    friend = ContactFactory(channel=personal, consent_status="granted", consent_source="crs")
    assert friend.can_receive_reply() and not friend.can_contact_proactively()


def test_model_clean_rules(tenant_a, tenant_b):
    contact = Contact(
        tenant=tenant_a, channel=ChannelFactory(tenant=tenant_b), zalo_user_id="z", display_name="x"
    )
    with pytest.raises(ValidationError):
        contact.clean()
    contact = Contact(
        tenant=tenant_a,
        channel=ChannelFactory(tenant=tenant_a),
        zalo_user_id="z",
        display_name="x",
        consent_status="granted",
    )
    with pytest.raises(ValidationError):
        contact.clean()


def test_contact_groups_crud(client_a, channel):
    c1, c2 = ContactFactory(channel=channel), ContactFactory(channel=channel)
    response = client_a.post(
        "/api/contact-groups/",
        {"name": "Khách", "contacts": [c1.pk, c2.pk], "default_permission": "auto_reply"},
        format="json",
    )
    assert response.status_code == 201
    group_id = response.json()["id"]
    assert sorted(response.json()["contacts"]) == sorted([c1.pk, c2.pk])
    log = AuditLog.objects.get(action="contact_group.created")
    assert sorted(log.changes["contacts"]) == sorted([c1.pk, c2.pk])
    assert client_a.patch(
        f"/api/contact-groups/{group_id}/", {"contacts": [c1.pk]}, format="json"
    ).json()["contacts"] == [c1.pk]
    assert client_a.delete(f"/api/contact-groups/{group_id}/").status_code == 204


def test_filter_contacts_by_consent(client_a, channel):
    ContactFactory(channel=channel, consent_status="granted", consent_source="crs")
    ContactFactory(channel=channel)
    assert client_a.get("/api/contacts/?consent_status=granted").json()["count"] == 1
