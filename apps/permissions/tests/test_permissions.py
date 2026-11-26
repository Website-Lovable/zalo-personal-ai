import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.permissions.models import ApprovalQueue, PermissionRule
from common.models import AuditLog
from conftest import auth_client
from tests.factories import ApprovalFactory, ContactGroupFactory, MessageFactory

pytestmark = pytest.mark.django_db


def test_rules_crud(client_a, tenant_a):
    group = ContactGroupFactory(tenant=tenant_a)
    response = client_a.post(
        "/api/permissions/rules/",
        {
            "name": "Giá cần duyệt",
            "priority": 100,
            "action": "need_approval",
            "keyword_triggers": ["giá", "hợp đồng"],
            "contact_group": group.pk,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    rule_id = response.json()["id"]
    client_a.post(
        "/api/permissions/rules/",
        {"name": "Low", "priority": 1, "action": "auto_reply"},
        format="json",
    )
    names = [r["name"] for r in client_a.get("/api/permissions/rules/").json()["results"]]
    assert names == ["Giá cần duyệt", "Low"]  # higher priority first
    assert (
        client_a.patch(
            f"/api/permissions/rules/{rule_id}/", {"is_active": False}, format="json"
        ).status_code
        == 200
    )
    assert client_a.delete(f"/api/permissions/rules/{rule_id}/").status_code == 204
    assert AuditLog.objects.filter(action__startswith="permission_rule.").count() == 4


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "action": "auto_reply", "keyword_triggers": "giá"},
        {"name": "x", "action": "auto_reply", "time_range_start": "08:00"},
        {"name": "x", "action": "maybe"},
    ],
)
def test_rule_validation(client_a, payload):
    assert client_a.post("/api/permissions/rules/", payload, format="json").status_code == 400


def test_rule_model_clean(tenant_a):
    rule = PermissionRule(
        tenant=tenant_a, name="x", action="auto_reply", time_range_start=timezone.now().time()
    )
    with pytest.raises(ValidationError):
        rule.clean()
    assert str(rule) == "x"


def test_approve_reject_and_modify(client_a, owner_a, tenant_a):
    msg = MessageFactory(conversation__contact__channel__tenant=tenant_a)
    item = ApprovalFactory(message=msg, owner_staff_ref="staff-07")
    listed = client_a.get("/api/approvals/?owner_staff_ref=staff-07").json()
    assert listed["count"] == 1

    response = client_a.post(
        f"/api/approvals/{item.pk}/approve/", {"modified_response": "Bản đã sửa"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "modified"
    assert response.json()["reviewed_by"] == owner_a.pk
    log = AuditLog.objects.get(action="approval.modified")
    assert log.changes["owner_staff_ref"] == "staff-07"
    assert client_a.post(f"/api/approvals/{item.pk}/reject/").status_code == 400

    other = ApprovalFactory(message=MessageFactory(conversation__contact__channel__tenant=tenant_a))
    assert client_a.post(f"/api/approvals/{other.pk}/approve/").json()["status"] == "approved"
    third = ApprovalFactory(message=MessageFactory(conversation__contact__channel__tenant=tenant_a))
    assert (
        client_a.post(
            f"/api/approvals/{third.pk}/reject/", {"reason": "sai giá"}, format="json"
        ).json()["status"]
        == "rejected"
    )


def test_expired_approval_cannot_be_approved(client_a, tenant_a):
    item = ApprovalFactory(
        message=MessageFactory(conversation__contact__channel__tenant=tenant_a),
        expires_at=timezone.now() - timezone.timedelta(minutes=1),
    )
    assert client_a.post(f"/api/approvals/{item.pk}/approve/").status_code == 400
    item.refresh_from_db()
    assert item.status == ApprovalQueue.Status.EXPIRED


def test_admin_can_approve_viewer_can_list(admin_a, viewer_a, tenant_a):
    item = ApprovalFactory(message=MessageFactory(conversation__contact__channel__tenant=tenant_a))
    assert auth_client(viewer_a).get("/api/approvals/").json()["count"] == 1
    assert auth_client(admin_a).post(f"/api/approvals/{item.pk}/approve/").status_code == 200
