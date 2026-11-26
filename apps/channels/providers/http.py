"""Shared HTTP plumbing for the official Zalo APIs (OA Open API, OAuth v4, Bot Platform).

Tests and local development swap the transport for the in-process fake Zalo
(``apps.channels.testing.fake_zalo``) with ``use_transport``; nothing else changes.
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx
from django.conf import settings

_transport: httpx.BaseTransport | None = None


class ZaloAPIError(Exception):
    """An error returned by a Zalo API (non-zero ``error`` / ``ok: false``) or a network error."""

    def __init__(self, message: str, *, code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.code = code
        self.payload = payload


class ZaloAuthError(ZaloAPIError):
    """Missing, invalid or expired credentials. The channel must be (re)connected."""


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=settings.ZALO_HTTP_TIMEOUT_SECONDS,
        transport=_transport,
        headers={"User-Agent": "zalo-personal-ai/0.2"},
    )


@contextlib.contextmanager
def use_transport(transport: httpx.BaseTransport | None):
    """Route every Zalo HTTP call through ``transport`` (e.g. ``httpx.MockTransport``)."""
    global _transport
    previous, _transport = _transport, transport
    try:
        yield
    finally:
        _transport = previous


def request_json(method: str, url: str, **kwargs) -> Any:
    try:
        with http_client() as client:
            response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ZaloAPIError(f"Network error calling Zalo: {exc.__class__.__name__}") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise ZaloAPIError(
            f"Zalo returned non-JSON response (HTTP {response.status_code})",
            code=response.status_code,
        ) from exc
