"""AIVA client interface.

- ``DisabledAIVAClient`` (mode off, or live without approval): never touches the network.
- ``MockAIVAClient``: in-memory, for tests, contract tests and sandbox runs without AIVA.
- ``HTTPAIVAClient`` (sandbox/live): signed webhook POSTs to AIVA (Kickoff 2.3 / 2.4).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

from common.exceptions import FeatureDisabledError

from . import config
from .schema import validate_envelope
from .signing import sign_headers

#: HTTP codes worth retrying. Any other 4xx is a contract problem: retrying will not help.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status_code: int | None = None
    detail: str = ""
    retryable: bool = True


class AIVAClient(ABC):
    @abstractmethod
    def send_event(self, envelope: dict[str, Any]) -> SendResult: ...

    @abstractmethod
    def health(self) -> bool: ...


class DisabledAIVAClient(AIVAClient):
    def __init__(self, reason: str = "AIVA_INTEGRATION_MODE=off: no calls to AIVA"):
        self.reason = reason

    def send_event(self, envelope):
        raise FeatureDisabledError(self.reason)

    def health(self) -> bool:
        return False


class MockAIVAClient(AIVAClient):
    def __init__(self, *, fail_with: int | None = None):
        self.fail_with = fail_with
        self.received: list[dict[str, Any]] = []

    def send_event(self, envelope):
        validate_envelope(envelope)
        if self.fail_with:
            return SendResult(
                ok=False,
                status_code=self.fail_with,
                detail="mock failure",
                retryable=self.fail_with in RETRYABLE_STATUS,
            )
        if any(e["event_id"] == envelope["event_id"] for e in self.received):
            return SendResult(ok=True, status_code=200, detail="duplicate")  # idempotent
        self.received.append(envelope)
        return SendResult(ok=True, status_code=202)

    def health(self) -> bool:
        return True


def encode_envelope(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()


class HTTPAIVAClient(AIVAClient):
    """POST ``{base_url}{events_path}`` with ``X-Signature``/``X-Timestamp`` (HMAC-SHA256),
    ``Idempotency-Key`` = event_id. AIVA must treat a repeated event_id as already done."""

    def __init__(
        self,
        *,
        base_url: str,
        secret: str,
        token: str = "",
        events_path: str = "/events",
        health_path: str = "/health",
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not base_url:
            raise FeatureDisabledError("AIVA_BASE_URL is not set")
        if not secret:
            raise FeatureDisabledError("AIVA_WEBHOOK_SECRET is not set")
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.token = token
        self.events_path = events_path
        self.health_path = health_path
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self.transport)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def send_event(self, envelope):
        validate_envelope(envelope)
        body = encode_envelope(envelope)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Idempotency-Key": envelope["event_id"],
            "X-Event-Type": envelope["event_type"],
            "X-Schema-Version": envelope["schema_version"],
            "X-Tenant-Ref": envelope["tenant_ref"],
            **self._auth_headers(),
            **sign_headers(self.secret, body),
        }
        try:
            with self._client() as client:
                response = client.post(
                    self.base_url + self.events_path, content=body, headers=headers
                )
        except httpx.HTTPError as exc:
            return SendResult(ok=False, detail=f"network: {exc.__class__.__name__}: {exc}"[:500])
        code = response.status_code
        if 200 <= code < 300 or code == 409:  # 409 = AIVA already has this event_id
            return SendResult(ok=True, status_code=code, detail="duplicate" if code == 409 else "")
        return SendResult(
            ok=False,
            status_code=code,
            detail=response.text[:500],
            retryable=code in RETRYABLE_STATUS,
        )

    def health(self) -> bool:
        try:
            with self._client() as client:
                response = client.get(
                    self.base_url + self.health_path, headers=self._auth_headers()
                )
        except httpx.HTTPError:
            return False
        return response.status_code == 200


#: Shared in-process mock used when AIVA_CLIENT=mock (sandbox without a running AIVA).
SANDBOX_MOCK = MockAIVAClient()


def get_aiva_client() -> AIVAClient:
    mode = config.get_mode()
    if mode == config.OFF:
        return DisabledAIVAClient()
    if mode == config.LIVE and not config.live_approved():
        return DisabledAIVAClient("AIVA live mode needs owner approval (AIVA_LIVE_APPROVED)")
    if config.client_backend() == "mock":
        if mode == config.LIVE:
            return DisabledAIVAClient("AIVA_CLIENT=mock is not allowed in live mode")
        return SANDBOX_MOCK
    return HTTPAIVAClient(
        base_url=settings.AIVA_BASE_URL,
        secret=settings.AIVA_WEBHOOK_SECRET,
        token=settings.AIVA_OUTBOUND_TOKEN,
        events_path=settings.AIVA_EVENTS_PATH,
        health_path=settings.AIVA_HEALTH_PATH,
        timeout=settings.AIVA_HTTP_TIMEOUT_SECONDS,
    )
