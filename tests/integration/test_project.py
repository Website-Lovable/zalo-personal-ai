"""Project-level acceptance checks: OpenAPI export, seed command, health, no secrets."""

import re
import subprocess
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.accounts.models import Tenant, User
from apps.channels.models import Channel
from apps.contacts.models import Contact
from apps.permissions.models import ApprovalQueue
from common.audit import redact

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]


def test_openapi_schema_exports(client_a, tmp_path):
    response = client_a.get("/api/schema/?format=json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in [
        "/api/channels/",
        "/api/contacts/",
        "/api/contact-groups/",
        "/api/conversations/",
        "/api/permissions/rules/",
        "/api/ai/profile/",
        "/api/ai/style/",
        "/api/auth/login/",
        "/api/approvals/{id}/approve/",
        "/api/channels/{id}/acknowledge-risk/",
        "/api/owner-sends/",
    ]:
        assert path in paths, path
    out = tmp_path / "openapi.yaml"
    call_command("spectacular", "--file", str(out), "--validate", "--fail-on-warn")
    assert out.read_text().startswith("openapi:")


def test_healthz(client):
    assert client.get("/healthz/").json() == {"status": "ok"}


def test_seed_demo_creates_only_demo_data():
    out = StringIO()
    call_command("seed_demo", "--password", "Demo-pass-2026", stdout=out)
    tenant = Tenant.objects.get(slug="aiva-demo")
    assert User.objects.filter(tenant=tenant).count() == 3
    assert Channel.objects.for_tenant(tenant).count() == 3
    for model in (Channel, Contact, ApprovalQueue):
        assert set(model.objects.for_tenant(tenant).values_list("data_origin", flat=True)) == {
            "demo"
        }
    personal = Channel.objects.for_tenant(tenant).get(channel_type="zalo_personal")
    assert personal.mode == "assist"
    assert ApprovalQueue.objects.for_tenant(tenant).get().owner_staff_ref == "staff-demo-01"
    assert User.objects.get(username="demo_owner").check_password("Demo-pass-2026")

    call_command("seed_demo", stdout=out)  # idempotent
    assert Tenant.objects.filter(slug="aiva-demo").count() == 1
    call_command("seed_demo", "--reset", stdout=out)
    assert "Mật khẩu" in out.getvalue()
    assert Tenant.objects.filter(slug="aiva-demo").count() == 1


def test_audit_redacts_secrets():
    clean = redact(
        {
            "password": "x",
            "access_token": "y",
            "name": "ok",
            "n": 1,
            "obj": object(),
            "nested": {"a": [1, 2]},
        }
    )
    assert clean["password"] == "***" and clean["access_token"] == "***"
    assert clean["name"] == "ok" and clean["nested"] == {"a": [1, 2]}
    assert isinstance(clean["obj"], str)


def test_no_secrets_committed():
    """Acceptance criterion 5: tracked files contain no obvious credentials."""
    try:
        files = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    patterns = [
        re.compile(p)
        for p in (
            r"sk-ant-[A-Za-z0-9_-]{20,}",
            r"sk-[A-Za-z0-9]{32,}",
            r"AKIA[0-9A-Z]{16}",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"ghp_[A-Za-z0-9]{30,}",
        )
    ]
    assert ".env" not in files
    for name in files:
        path = ROOT / name
        if not path.is_file() or path.suffix in {".png", ".jpg", ".ico"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            assert not pattern.search(text), f"possible secret in {name}"
