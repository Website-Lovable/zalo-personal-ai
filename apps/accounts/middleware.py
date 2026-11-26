"""Tenant middleware: resolves ``request.tenant`` for every request.

DRF authenticates JWT inside the view, so the middleware resolves the tenant lazily from
either the session user or the Bearer token. Requests to ``/api/`` from a user whose tenant
is inactive are rejected here (defence in depth; the API permission classes check again).
"""

from django.http import JsonResponse
from django.utils.functional import SimpleLazyObject
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


def _resolve_user(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer "):
        return None
    try:
        result = JWTAuthentication().authenticate(request)
    except (InvalidToken, TokenError):
        return None
    return result[0] if result else None


def _resolve_tenant(request):
    user = _resolve_user(request)
    return getattr(user, "tenant", None) if user is not None else None


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = SimpleLazyObject(lambda: _resolve_tenant(request))
        if request.path.startswith("/api/") and not request.path.startswith("/api/auth/"):
            tenant = request.tenant
            # SimpleLazyObject wrapping None is truthy-safe: compare the wrapped value.
            if tenant and not tenant.is_active:
                return JsonResponse({"detail": "Tenant đã bị khóa."}, status=403)
        return self.get_response(request)
