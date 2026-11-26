"""Phase 5 security review: one regression test per fixed finding (see the review doc)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import RequestFactory
from rest_framework.test import APIClient

from common.audit import client_ip
from common.models import AuditLog

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]


# --- S1: brute force on public auth endpoints --------------------------------------------


def test_login_is_throttled(owner_a, settings):
    client = APIClient()
    for _ in range(10):
        response = client.post(
            "/api/auth/login/", {"username": owner_a.username, "password": "bad"}, format="json"
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/auth/login/",
        {"username": owner_a.username, "password": "S3cure-pass-123"},
        format="json",
    )
    assert blocked.status_code == 429


def test_failed_logins_are_audited(owner_a):
    APIClient().post(
        "/api/auth/login/", {"username": owner_a.username, "password": "bad"}, format="json"
    )
    APIClient().post("/api/auth/login/", {"username": "ghost", "password": "x"}, format="json")
    rows = AuditLog.objects.filter(action="auth.login_failed").order_by("id")
    assert [r.changes["username"] for r in rows] == [owner_a.username, "ghost"]
    assert rows[0].tenant == owner_a.tenant and rows[1].tenant is None
    assert "bad" not in str(list(rows.values("changes")))


def test_register_and_refresh_are_throttled():
    client = APIClient()
    codes = [
        client.post(
            "/api/auth/register/",
            {"tenant_name": f"T{i}", "username": f"u{i}", "password": "Str0ng-pass-2026"},
            format="json",
        ).status_code
        for i in range(6)
    ]
    assert codes == [201] * 5 + [429]
    codes = {
        client.post("/api/auth/refresh-token/", {"refresh": "x"}, format="json").status_code
        for _ in range(31)
    }
    assert 429 in codes


# --- S2: forged X-Forwarded-For ------------------------------------------------------------


def test_client_ip_ignores_forwarded_header_without_trusted_proxy(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="1.2.3.4")
    assert client_ip(request) == "10.0.0.5"


def test_client_ip_takes_entry_added_by_our_proxy(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    forged = RequestFactory().get(
        "/", REMOTE_ADDR="172.18.0.3", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9"
    )
    assert client_ip(forged) == "203.0.113.9"
    settings.TRUSTED_PROXY_COUNT = 5  # more proxies configured than hops: left-most hop
    assert client_ip(forged) == "6.6.6.6"
    plain = RequestFactory().get("/", REMOTE_ADDR="172.18.0.3")
    assert client_ip(plain) == "172.18.0.3"
    assert client_ip(None) is None


# --- S3/S5: production settings refuse unsafe configuration --------------------------------

GOOD = {
    "SECRET_KEY": "p" * 60,
    "JWT_SECRET_KEY": "j" * 60,
    "ENCRYPTION_KEY": "q2bEqgCFV9ZbRm1Gx2Qh3oN8bxQ0m7ZsQyKxTQkUo5k=",
    "DATABASE_URL": "postgres://u:p@db:5432/zalo_ai",
    "ALLOWED_HOSTS": "zalo.example.com",
}


def _load_production(**overrides) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in GOOD}
    env.update(GOOD)
    env.update(overrides)
    env = {k: v for k, v in env.items() if v is not None}
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.production"
    code = (
        "import django; django.setup(); from django.conf import settings as s; "
        "print(s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_INCLUDE_SUBDOMAINS, s.CSRF_TRUSTED_ORIGINS, "
        "s.TRUSTED_PROXY_COUNT, s.ALLOWED_HOSTS, "
        "s.CACHES['default']['BACKEND'].rsplit('.', 1)[-1], "
        "s.SPECTACULAR_SETTINGS['SERVE_PERMISSIONS'], s.SECURE_REDIRECT_EXEMPT)"
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True
    )


def test_production_settings_load_with_safe_defaults():
    result = _load_production()
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.startswith(
        "True True ['https://zalo.example.com'] 1 "
        "['zalo.example.com', 'web', '127.0.0.1', 'localhost'] RedisCache"
    )
    assert "IsAuthenticated" in out and "^healthz/$" in out and "^metrics/$" in out


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"JWT_SECRET_KEY": None}, "JWT_SECRET_KEY must be set"),
        ({"SECRET_KEY": "change-me"}, "too short or a placeholder"),
        ({"JWT_SECRET_KEY": "p" * 60}, "must differ"),
        ({"ENCRYPTION_KEY": "not-a-key"}, "valid Fernet key"),
        ({"ALLOWED_HOSTS": None}, "ALLOWED_HOSTS"),
    ],
)
def test_production_settings_refuse_unsafe_values(overrides, message):
    result = _load_production(**overrides)
    assert result.returncode != 0 and message in result.stderr


def test_every_setting_is_documented_in_env_example():
    """CLAUDE.md rule 8: .env.example lists every variable the settings read."""
    import re

    read = set()
    for path in (ROOT / "config" / "settings").glob("*.py"):
        read |= set(re.findall(r'env(?:_bool|_list)?\("([A-Z0-9_]+)"', path.read_text()))
    documented = set(re.findall(r"^([A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    assert read - documented == set()
