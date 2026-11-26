import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Tenant, User
from common.models import AuditLog
from conftest import auth_client
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_register_creates_tenant_and_owner():
    client = APIClient()
    response = client.post(
        "/api/auth/register/",
        {
            "tenant_name": "Công ty Test",
            "username": "boss",
            "password": "Very-strong-pass-1",
        },
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["access"] and body["refresh"]
    user = User.objects.get(username="boss")
    assert user.role == "owner"
    assert user.tenant.slug == "cong-ty-test"
    assert AuditLog.objects.filter(action="tenant.registered", tenant=user.tenant).exists()


def test_register_slug_is_unique_and_username_checked():
    client = APIClient()
    payload = {"tenant_name": "Dup", "username": "u1", "password": "Very-strong-pass-1"}
    assert client.post("/api/auth/register/", payload, format="json").status_code == 201
    assert client.post("/api/auth/register/", payload, format="json").status_code == 400
    payload["username"] = "u2"
    assert client.post("/api/auth/register/", payload, format="json").status_code == 201
    assert Tenant.objects.filter(slug="dup-2").exists()


def test_register_rejects_weak_password():
    response = APIClient().post(
        "/api/auth/register/",
        {"tenant_name": "X", "username": "weak", "password": "12345678"},
        format="json",
    )
    assert response.status_code == 400


def test_login_refresh_and_me(owner_a):
    client = APIClient()
    response = client.post(
        "/api/auth/login/",
        {"username": owner_a.username, "password": "S3cure-pass-123"},
        format="json",
    )
    assert response.status_code == 200
    tokens = response.json()
    assert tokens["role"] == "owner"
    assert tokens["tenant_id"] == owner_a.tenant_id
    assert AuditLog.objects.filter(action="auth.login", user=owner_a).exists()

    refreshed = client.post(
        "/api/auth/refresh-token/", {"refresh": tokens["refresh"]}, format="json"
    )
    assert refreshed.status_code == 200

    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    me = client.get("/api/auth/me/")
    assert me.json()["username"] == owner_a.username
    assert "password" not in me.json()


def test_login_wrong_password(owner_a):
    response = APIClient().post(
        "/api/auth/login/", {"username": owner_a.username, "password": "nope"}, format="json"
    )
    assert response.status_code == 401


def test_api_requires_authentication():
    assert APIClient().get("/api/contacts/").status_code == 401


def test_user_without_tenant_is_forbidden(db):
    user = UserFactory(tenant=None)
    assert auth_client(user).get("/api/contacts/").status_code == 403


def test_inactive_tenant_is_blocked_by_middleware(owner_a, tenant_a):
    tenant_a.is_active = False
    tenant_a.save()
    response = auth_client(owner_a).get("/api/contacts/")
    assert response.status_code == 403


def test_invalid_bearer_token_is_rejected():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
    assert client.get("/api/contacts/").status_code == 401


def test_tenant_view_get_and_owner_patch(client_a, tenant_a, admin_a):
    assert client_a.get("/api/tenant/").json()["name"] == "Tenant A"
    response = client_a.patch(
        "/api/tenant/", {"external_ref": "crs-new", "slug": "hack"}, format="json"
    )
    assert response.status_code == 200
    tenant_a.refresh_from_db()
    assert tenant_a.external_ref == "crs-new"
    assert tenant_a.slug != "hack"
    assert AuditLog.objects.filter(action="tenant.updated").exists()
    assert (
        auth_client(admin_a).patch("/api/tenant/", {"name": "x"}, format="json").status_code == 403
    )


def test_owner_manages_users(client_a, tenant_a, admin_a):
    response = client_a.post(
        "/api/users/",
        {"username": "newbie", "role": "viewer", "password": "Another-pass-99"},
        format="json",
    )
    assert response.status_code == 201
    new_user = User.objects.get(username="newbie")
    assert new_user.tenant == tenant_a
    assert new_user.check_password("Another-pass-99")
    assert (
        client_a.patch(
            f"/api/users/{new_user.pk}/",
            {"role": "admin", "password": "Changed-pass-77"},
            format="json",
        ).status_code
        == 200
    )
    new_user.refresh_from_db()
    assert new_user.role == "admin" and new_user.check_password("Changed-pass-77")
    assert client_a.delete(f"/api/users/{new_user.pk}/").status_code == 204
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"user.created", "user.updated", "user.deleted"} <= actions

    admin_client = auth_client(admin_a)
    assert admin_client.get("/api/users/").status_code == 200
    assert (
        admin_client.post(
            "/api/users/", {"username": "x", "role": "owner"}, format="json"
        ).status_code
        == 403
    )


def test_user_created_without_password_cannot_login(client_a):
    client_a.post("/api/users/", {"username": "nopass", "role": "viewer"}, format="json")
    assert not User.objects.get(username="nopass").has_usable_password()


def test_user_has_role_helper(owner_a, viewer_a):
    assert owner_a.has_role("admin")
    assert not viewer_a.has_role("admin")
