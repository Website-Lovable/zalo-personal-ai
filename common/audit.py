"""Audit logging helper. Use for every significant action and every AIVA exchange."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from .models import AuditLog

# Never write these values into the audit trail.
_REDACTED_FIELDS = {"password", "access_token", "refresh_token", "session_data", "secret"}


def client_ip(request) -> str | None:
    """The caller's IP. X-Forwarded-For is only trusted for the TRUSTED_PROXY_COUNT reverse
    proxies we run ourselves (Caddy in production = 1): the left-most entries are written by
    the client and can be forged, so we take the entry our own proxy appended."""
    if request is None:
        return None
    proxies = settings.TRUSTED_PROXY_COUNT
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if proxies > 0 and forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-min(proxies, len(hops))]
    return request.META.get("REMOTE_ADDR")


def redact(changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (changes or {}).items():
        if any(secret in key.lower() for secret in _REDACTED_FIELDS):
            clean[key] = "***"
        elif isinstance(value, models.Model):
            clean[key] = value.pk
        elif isinstance(value, (list, tuple, set)) or hasattr(value, "all"):
            items = value.all() if hasattr(value, "all") else value
            clean[key] = [v.pk if isinstance(v, models.Model) else _jsonable(v) for v in items]
        else:
            clean[key] = _jsonable(value)
    return clean


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def log_action(
    action: str,
    *,
    tenant=None,
    user=None,
    obj: models.Model | None = None,
    changes: dict[str, Any] | None = None,
    request=None,
) -> AuditLog:
    if request is not None and user is None and getattr(request, "user", None) is not None:
        user = request.user if request.user.is_authenticated else None
    if tenant is None and obj is not None:
        tenant = getattr(obj, "tenant", None)
    if tenant is None and user is not None:
        tenant = getattr(user, "tenant", None)
    return AuditLog.objects.create(
        tenant=tenant,
        user=user,
        action=action,
        model_name=obj._meta.label if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        changes=redact(changes or {}),
        ip_address=client_ip(request),
    )
