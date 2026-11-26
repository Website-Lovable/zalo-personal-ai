"""Authentication of AIVA -> Zalo AI requests (Kickoff 2.2): service token + HMAC.

Headers sent by AIVA:
- ``Authorization: Bearer <AIVA_SERVICE_TOKEN>``
- ``X-Tenant-Ref: <Tenant.external_ref>``
- ``X-Timestamp: <unix seconds>`` and ``X-Signature: sha256=<hex>`` over
  ``f"{timestamp}.{METHOD}.{path}.{tenant_ref}." + raw_body`` with ``AIVA_INBOUND_SECRET``
- ``Idempotency-Key`` on every POST.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from apps.accounts.models import Tenant

from . import config, live
from .models import IntegrationAuditLog
from .signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, verify_request_signature

TENANT_HEADER = "X-Tenant-Ref"


class IntegrationOff(exceptions.APIException):
    status_code = 503
    default_detail = "Tích hợp AIVA đang tắt (AIVA_INTEGRATION_MODE)."
    default_code = "integration_off"


class AIVAService:
    """The authenticated caller: AIVA acting for one tenant."""

    is_authenticated = True
    is_anonymous = False
    role = "service"
    staff_ref = ""
    pk = None

    def __init__(self, tenant: Tenant):
        self.tenant = tenant

    def __str__(self):
        return f"aiva-service:{self.tenant.external_ref}"


def audit_inbound(request, *, tenant=None, success: bool, status: int, detail: str = "", **kw):
    IntegrationAuditLog.objects.create(
        tenant=tenant,
        direction=IntegrationAuditLog.Direction.INBOUND,
        mode=config.get_mode(),
        event_type=kw.get("event_type") or f"{request.method} {request.path}"[:50],
        data_origin=kw.get("data_origin", ""),
        success=success,
        status_code=status,
        detail=detail[:1000],
    )


def _fail(request, message: str, tenant=None):
    audit_inbound(request, tenant=tenant, success=False, status=401, detail=f"auth: {message}")
    raise exceptions.AuthenticationFailed(message)


class AIVAServiceAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Bearer realm="aiva-integration"'

    def authenticate(self, request):
        try:
            config.ensure_mode_usable()
        except Exception as exc:
            raise IntegrationOff() from exc
        token = settings.AIVA_SERVICE_TOKEN
        secret = settings.AIVA_INBOUND_SECRET
        if not token or not secret:
            raise IntegrationOff("Chưa cấu hình AIVA_SERVICE_TOKEN / AIVA_INBOUND_SECRET.")

        header = request.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else ""
        if not presented or not hmac.compare_digest(presented, token):
            _fail(request, "invalid service token")
        tenant_ref = request.headers.get(TENANT_HEADER, "").strip()
        tenant = (
            Tenant.objects.filter(external_ref=tenant_ref, is_active=True).first()
            if tenant_ref
            else None
        )
        if tenant is None:
            _fail(request, "unknown tenant")
        try:
            verify_request_signature(
                secret,
                request.method,
                request.get_full_path(),
                tenant_ref,
                request.body,
                request.headers.get(SIGNATURE_HEADER),
                request.headers.get(TIMESTAMP_HEADER),
                tolerance_seconds=settings.AIVA_SIGNATURE_TOLERANCE_SECONDS,
            )
        except SignatureError as exc:
            _fail(request, f"signature: {exc}", tenant)
        allowed, reason = live.can_exchange(tenant)
        if not allowed:  # Phase 6: tenant not activated, paused, or platform switch paused
            audit_inbound(request, tenant=tenant, success=False, status=503, detail=reason)
            raise IntegrationOff(f"Kết nối live chưa mở cho tenant này ({reason}).")
        return AIVAService(tenant), None


class AIVAServiceAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.integrations.aiva.inbound_auth.AIVAServiceAuthentication"
    name = "aivaService"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "AIVA service token, plus X-Tenant-Ref, X-Timestamp and X-Signature "
                "(HMAC-SHA256, see docs/integration/AIVA_INTEGRATION_V1.md)."
            ),
        }
