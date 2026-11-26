"""AIVA client interface.

- ``DisabledAIVAClient`` (mode off): never touches the network; every send is refused.
- ``MockAIVAClient``: in-memory, for tests and contract tests.
- The HTTP client for sandbox/live is implemented in Phase 3.5.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from common.exceptions import FeatureDisabledError, NotImplementedInPhase

from . import config
from .schema import validate_envelope


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status_code: int | None = None
    detail: str = ""


class AIVAClient(ABC):
    @abstractmethod
    def send_event(self, envelope: dict[str, Any]) -> SendResult: ...

    @abstractmethod
    def health(self) -> bool: ...


class DisabledAIVAClient(AIVAClient):
    def send_event(self, envelope):
        raise FeatureDisabledError("AIVA_INTEGRATION_MODE=off: no calls to AIVA")

    def health(self) -> bool:
        return False


class MockAIVAClient(AIVAClient):
    def __init__(self, *, fail_with: int | None = None):
        self.fail_with = fail_with
        self.received: list[dict[str, Any]] = []

    def send_event(self, envelope):
        validate_envelope(envelope)
        if self.fail_with:
            return SendResult(ok=False, status_code=self.fail_with, detail="mock failure")
        self.received.append(envelope)
        return SendResult(ok=True, status_code=202)

    def health(self) -> bool:
        return True


def get_aiva_client() -> AIVAClient:
    if config.get_mode() == config.OFF:
        return DisabledAIVAClient()
    raise NotImplementedInPhase("HTTP AIVA client (sandbox/live)", "3.5")
