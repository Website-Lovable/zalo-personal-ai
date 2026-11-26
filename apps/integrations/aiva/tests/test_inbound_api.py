"""Phase 3.5: AIVA -> Zalo AI endpoints (/api/integration/v1/), auth, guards, contract."""

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.channels.providers.mock import MockChannelProvider
from apps.contacts.models import Contact
from apps.integrations.aiva.inbound import validate
from apps.integrations.aiva.models import IntegrationAuditLog, IntegrationInboundRequest
from apps.memory.models import ShortTermMemory
from apps.permissions.models import ApprovalQueue
from common.models import AuditLog
from conftest import auth_client
from tests.factories import (
    ChannelFactory,
    ContactFactory,
    ConversationFactory,
    MessageFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db
NOW_ISO = "2026-11-26T10:00:00+07:00"


def _upsert_body(channel, **extra):
    return {
        "data_origin": "demo",
        "crs_customer_id": "CRS-NEW",
        "display_name": "Anh Tuấn (demo)",
        "segment": "b2c",
        "owner_staff_ref": "staff-07",
        "consent_status": "granted",
        "consent_at": NOW_ISO,
        "channel_ref": str(channel.pk),
        "zalo_user_id": "zalo-tuan",
        **extra,
    }


# --- Mode and authentication ---------------------------------------------------------


def test_everything_is_503_while_off(aiva, tenant_a):
    assert aiva("GET", "health").status_code == 503
    assert aiva("POST", "contacts/upsert", {}).status_code == 503
    assert IntegrationAuditLog.objects.count() == 0


def test_not_configured_is_503(aiva, sandbox, tenant_a, settings):
    settings.AIVA_INBOUND_SECRET = ""
    assert aiva("GET", "health").status_code == 503


def test_health(aiva, sandbox, tenant_a):
    response = aiva("GET", "health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "sandbox" and body["tenant_ref"] == "crs-a"
    assert validate("health_response", body) == []
    log = IntegrationAuditLog.objects.get()
    assert log.direction == "inbound" and log.success and log.tenant == tenant_a


@pytest.mark.parametrize(
    "kwargs",
    [
        {"token": "wrong"},
        {"secret": "wrong"},
        {"tenant_ref": "crs-unknown"},
        {"now": time.time() - 3600},  # replay of an old signed request
        {"sign_path": "/api/integration/v1/other"},  # signature made for another path
    ],
)
def test_bad_credentials_are_refused_and_audited(aiva, sandbox, tenant_a, kwargs):
    response = aiva("GET", "health", **kwargs)
    assert response.status_code == 401
    log = IntegrationAuditLog.objects.get()
    assert not log.success and log.detail.startswith("auth:")


def test_signature_bound_to_tenant(aiva, sandbox, tenant_a, tenant_b, demo_conversation):
    # A request signed for tenant B cannot be replayed with tenant A's header.
    path = f"conversations/{demo_conversation.pk}/summary"
    from apps.integrations.aiva.signing import sign_request_headers

    from .conftest import INBOUND_SECRET, SERVICE_TOKEN

    full = f"/api/integration/v1/{path}"
    headers = sign_request_headers(INBOUND_SECRET, "GET", full, "crs-b", b"")
    response = aiva.client.get(
        full,
        HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}",
        HTTP_X_TENANT_REF="crs-a",
        HTTP_X_SIGNATURE=headers["X-Signature"],
        HTTP_X_TIMESTAMP=headers["X-Timestamp"],
    )
    assert response.status_code == 401


# --- contacts/upsert -------------------------------------------------------------------


def test_upsert_creates_contact_with_crs_consent(aiva, sandbox, demo_channel):
    response = aiva("POST", "contacts/upsert", _upsert_body(demo_channel))
    assert response.status_code == 201, response.json()
    body = response.json()
    assert validate("contacts_upsert_response", body) == []
    contact = Contact.objects.get(pk=body["contact_ref"])
    assert contact.data_origin == "demo" and contact.consent_status == "granted"
    assert contact.consent_source == "crs" and contact.consent_at.isoformat().startswith("2026")
    assert contact.owner_staff_ref == "staff-07" and contact.segment == "b2c"
    assert AuditLog.objects.filter(action="contact.crs_upserted", object_id=str(contact.pk))


def test_upsert_crs_wins_and_withdrawal(aiva, sandbox, demo_contact):
    body = {
        "data_origin": "demo",
        "crs_customer_id": "CRS-1",
        "display_name": "Tên mới từ CRS",
        "consent_status": "withdrawn",
        "consent_at": NOW_ISO,
    }
    response = aiva("POST", "contacts/upsert", body)
    assert response.status_code == 200 and response.json()["created"] is False
    demo_contact.refresh_from_db()
    assert demo_contact.display_name == "Tên mới từ CRS"
    assert demo_contact.consent_status == "withdrawn"
    assert demo_contact.owner_staff_ref == "staff-01"  # not sent -> unchanged


def test_upsert_links_existing_zalo_contact(aiva, sandbox, demo_channel):
    known = ContactFactory(channel=demo_channel, data_origin="demo", zalo_user_id="zalo-tuan")
    response = aiva("POST", "contacts/upsert", _upsert_body(demo_channel))
    assert response.status_code == 200 and response.json()["contact_ref"] == str(known.pk)
    known.refresh_from_db()
    assert known.crs_customer_id == "CRS-NEW"


@pytest.mark.parametrize(
    "mutate,status,code",
    [
        (lambda b, c: b.update(data_origin="real"), 422, "data_origin_not_allowed"),
        (lambda b, c: b.pop("display_name"), 400, "invalid_body"),
        (lambda b, c: b.update(consent_status="unknown"), 400, "invalid_body"),
        (lambda b, c: b.update(channel_ref="999999"), 422, "unknown_channel"),
        (lambda b, c: b.update(channel_ref="abc"), 422, "unknown_channel"),
        (lambda b, c: (b.pop("channel_ref"), b.pop("zalo_user_id")), 404, "contact_unknown"),
    ],
)
def test_upsert_rejections(aiva, sandbox, demo_channel, mutate, status, code):
    body = _upsert_body(demo_channel)
    mutate(body, demo_channel)
    response = aiva("POST", "contacts/upsert", body)
    assert response.status_code == status and response.json()["code"] == code
    assert validate("error", response.json()) == []
    assert not Contact.objects.filter(crs_customer_id="CRS-NEW").exists()


def test_upsert_never_mixes_demo_and_real(aiva, sandbox, tenant_a, demo_channel):
    real_channel = ChannelFactory(tenant=tenant_a, data_origin="real")
    response = aiva("POST", "contacts/upsert", _upsert_body(real_channel))
    assert response.status_code == 409 and response.json()["code"] == "data_origin_mismatch"
    real_contact = ContactFactory(channel=real_channel, crs_customer_id="CRS-REAL")
    body = _upsert_body(demo_channel, crs_customer_id=real_contact.crs_customer_id)
    response = aiva("POST", "contacts/upsert", body, key="key-2")
    assert response.status_code == 409


def test_upsert_crs_id_conflict(aiva, sandbox, demo_channel):
    ContactFactory(
        channel=demo_channel, data_origin="demo", zalo_user_id="zalo-tuan", crs_customer_id="X"
    )
    response = aiva("POST", "contacts/upsert", _upsert_body(demo_channel))
    assert response.status_code == 409 and response.json()["code"] == "crs_id_conflict"


def test_upsert_cannot_touch_other_tenant(aiva, sandbox, tenant_b, demo_channel):
    # Tenant B (valid credentials) points at tenant A's channel.
    response = aiva("POST", "contacts/upsert", _upsert_body(demo_channel), tenant_ref="crs-b")
    assert response.status_code == 422 and response.json()["code"] == "unknown_channel"


# --- Idempotency ----------------------------------------------------------------------


def test_idempotent_replay_and_key_reuse(aiva, sandbox, demo_channel):
    body = _upsert_body(demo_channel)
    first = aiva("POST", "contacts/upsert", body, key="same")
    again = aiva("POST", "contacts/upsert", body, key="same")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json() and again["Idempotent-Replayed"] == "true"
    assert Contact.objects.filter(crs_customer_id="CRS-NEW").count() == 1
    other = aiva("POST", "contacts/upsert", {**body, "display_name": "Khác"}, key="same")
    assert other.status_code == 409 and other.json()["code"] == "idempotency_key_reused"
    assert IntegrationInboundRequest.objects.count() == 1


def test_post_needs_idempotency_key_and_json(aiva, sandbox, demo_channel):
    response = aiva("POST", "contacts/upsert", _upsert_body(demo_channel), key="")
    assert response.status_code == 400 and response.json()["code"] == "idempotency_key_required"


def test_invalid_json_and_too_large(aiva, sandbox, tenant_a, client):
    from apps.integrations.aiva.signing import sign_request_headers

    from .conftest import INBOUND_SECRET, SERVICE_TOKEN

    def raw_post(raw):
        path = "/api/integration/v1/contacts/upsert"
        headers = sign_request_headers(INBOUND_SECRET, "POST", path, "crs-a", raw)
        return client.generic(
            "POST",
            path,
            raw,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}",
            HTTP_X_TENANT_REF="crs-a",
            HTTP_X_SIGNATURE=headers["X-Signature"],
            HTTP_X_TIMESTAMP=headers["X-Timestamp"],
            HTTP_IDEMPOTENCY_KEY="k",
        )

    assert raw_post(b"{not json").json()["code"] == "invalid_json"
    assert raw_post(b'{"a":"' + b"x" * 70_000 + b'"}').status_code == 413


# --- outreach/draft --------------------------------------------------------------------


def test_outreach_goes_to_staff_queue_and_is_not_sent(aiva, sandbox, demo_contact):
    body = {
        "data_origin": "demo",
        "crs_customer_id": "CRS-1",
        "draft_text": "Chào chị Lan, bên em có lớp AI mới khai giảng tháng 12 ạ.",
        "purpose": "care_followup",
        "request_ref": "aiva-req-1",
    }
    response = aiva("POST", "outreach/draft", body)
    assert response.status_code == 201, response.json()
    data = response.json()
    assert validate("outreach_draft_response", data) == []
    approval = ApprovalQueue.objects.get(pk=data["approval_ref"])
    assert approval.source == "aiva_outreach" and approval.status == "pending"
    assert approval.owner_staff_ref == "staff-01" and approval.data_origin == "demo"
    assert MockChannelProvider.sent == []  # never sent automatically

    # The staff member in charge (a viewer with the matching staff_ref) approves -> sent.
    staff = UserFactory(tenant=demo_contact.tenant, role="viewer", staff_ref="staff-01")
    other = UserFactory(tenant=demo_contact.tenant, role="viewer", staff_ref="staff-99")
    assert auth_client(other).post(f"/api/approvals/{approval.pk}/approve/").status_code == 403
    response = auth_client(staff).post(f"/api/approvals/{approval.pk}/approve/")
    assert response.status_code == 200 and response.json()["status"] == "approved"
    assert MockChannelProvider.sent[-1][2] == body["draft_text"]
    approval.refresh_from_db()
    assert approval.conversation is not None and approval.sent_message.was_approved


def test_outreach_staff_override_and_sensitive_flag(aiva, sandbox, demo_contact):
    body = {
        "data_origin": "demo",
        "contact_ref": str(demo_contact.pk),
        "draft_text": "Em gửi chị báo giá ưu đãi tháng này ạ.",
        "owner_staff_ref": "staff-02",
        "expires_in_hours": 2,
    }
    data = aiva("POST", "outreach/draft", body).json()
    approval = ApprovalQueue.objects.get(pk=data["approval_ref"])
    assert approval.owner_staff_ref == "staff-02"
    assert any(r.startswith("sensitive:") for r in approval.reasons)
    assert approval.expires_at < timezone.now() + timedelta(hours=3)


@pytest.mark.parametrize(
    "setup,status,code",
    [
        (lambda c: c.set_consent("withdrawn"), 422, "consent_required"),
        (lambda c: setattr(c, "reply_permission", "do_not_reply"), 422, "contact_do_not_contact"),
        (
            lambda c: setattr(c.channel, "is_active", False) or c.channel.save(),
            422,
            "channel_inactive",
        ),
        (
            lambda c: (
                type(c.channel)
                .objects.filter(pk=c.channel_id)
                .update(channel_type="zalo_personal", mode="assist")
            ),
            422,
            "personal_channel_outreach_forbidden",
        ),
        (lambda c: setattr(c, "data_origin", "real"), 409, "data_origin_mismatch"),
    ],
)
def test_outreach_refusals(aiva, sandbox, demo_contact, setup, status, code):
    setup(demo_contact)
    demo_contact.save()
    body = {"data_origin": "demo", "contact_ref": str(demo_contact.pk), "draft_text": "Chào"}
    response = aiva("POST", "outreach/draft", body)
    assert response.status_code == status and response.json()["code"] == code
    assert ApprovalQueue.objects.count() == 0


def test_outreach_unknown_or_foreign_contact(aiva, sandbox, tenant_b, demo_contact):
    body = {"data_origin": "demo", "contact_ref": str(demo_contact.pk), "draft_text": "Chào"}
    assert aiva("POST", "outreach/draft", body, tenant_ref="crs-b").status_code == 404
    body["contact_ref"] = "nope"
    assert aiva("POST", "outreach/draft", body, key="k2").status_code == 404
    assert aiva("POST", "outreach/draft", {"data_origin": "demo"}, key="k3").status_code == 400


def test_approved_outreach_rechecks_consent(aiva, sandbox, demo_contact, client_a):
    body = {"data_origin": "demo", "contact_ref": str(demo_contact.pk), "draft_text": "Chào"}
    approval_id = aiva("POST", "outreach/draft", body).json()["approval_ref"]
    demo_contact.set_consent("withdrawn")
    demo_contact.save()
    response = client_a.post(f"/api/approvals/{approval_id}/approve/")
    assert response.status_code == 400 and MockChannelProvider.sent == []


# --- conversations/{id}/summary ------------------------------------------------------


def test_summary_returns_only_the_summary(aiva, sandbox, demo_conversation):
    MessageFactory(conversation=demo_conversation, content="Số tài khoản của chị là 0123456789")
    ShortTermMemory.objects.create(
        tenant=demo_conversation.tenant,
        conversation=demo_conversation,
        context_summary="Chị hỏi lịch học cuối tuần.",
        key_points=["lịch học"],
        expires_at=timezone.now() + timedelta(hours=1),
        data_origin="demo",
    )
    response = aiva("GET", f"conversations/{demo_conversation.pk}/summary")
    assert response.status_code == 200
    body = response.json()
    assert validate("conversation_summary_response", body) == []
    assert body["summary"] == "Chị hỏi lịch học cuối tuần." and body["key_points"] == ["lịch học"]
    assert body["crs_customer_id"] == "CRS-1"
    assert "0123456789" not in response.content.decode()  # never message history


def test_summary_guards(aiva, sandbox, tenant_a, tenant_b, demo_conversation):
    path = f"conversations/{demo_conversation.pk}/summary"
    assert aiva("GET", path, tenant_ref="crs-b").status_code == 404  # other tenant
    empty = aiva("GET", path).json()
    assert empty["summary"] == "" and empty["summary_updated_at"] is None
    demo_conversation.contact.set_consent("withdrawn")
    demo_conversation.contact.save()
    assert aiva("GET", path).status_code == 403
    real = ConversationFactory(contact=ContactFactory(channel=ChannelFactory(tenant=tenant_a)))
    assert aiva("GET", f"conversations/{real.pk}/summary").status_code == 404  # real in sandbox


def test_openapi_lists_integration_endpoints(client_a):
    schema = client_a.get("/api/schema/").content.decode()
    for path in (
        "/api/integration/v1/contacts/upsert",
        "/api/integration/v1/health",
        "/api/integrations/aiva/outbox/",
    ):
        assert path in schema
