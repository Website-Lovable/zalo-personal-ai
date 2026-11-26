"""In-memory adapter for tests and local development. Never talks to Zalo.

Login: ``start_login`` returns a dummy QR; the first ``login_status`` poll says ``scanned``,
the second ``confirmed`` (tests can call ``confirm`` / ``expire`` directly).
Sending: every send is recorded in ``sent``; ``fail_next(...)`` scripts failures.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.utils import timezone

from .adapter import (
    AdapterSendResult,
    LoginChallenge,
    LoginState,
    LoginStatus,
    PersonalSessionAdapter,
    RestrictionDetected,
)

_PIXEL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakePersonalAdapter(PersonalSessionAdapter):
    name = "fake"

    #: login_id -> {"polls": int, "state": str, "account_id": str}
    logins: dict[str, dict] = {}
    #: (channel_id, thread_id, text)
    sent: list[tuple[int, str, str]] = []
    #: scripted outcomes for the next sends: "error" or a RestrictionDetected kind
    _script: list[str] = []
    account_id: str = "fake-zalo-account-1"
    display_name: str = "Chủ tài khoản (giả lập)"

    def start_login(self, channel) -> LoginChallenge:
        login_id = uuid.uuid4().hex
        FakePersonalAdapter.logins[login_id] = {
            "polls": 0,
            "state": LoginState.PENDING,
            "account_id": FakePersonalAdapter.account_id,
        }
        return LoginChallenge(
            login_id=login_id, qr_image=_PIXEL, expires_at=timezone.now() + timedelta(minutes=2)
        )

    def login_status(self, channel, login_id: str) -> LoginStatus:
        entry = FakePersonalAdapter.logins.get(login_id)
        if entry is None:
            return LoginStatus(state=LoginState.FAILED, error="unknown login")
        if entry["state"] not in LoginState.FINAL:
            entry["polls"] += 1
            entry["state"] = LoginState.SCANNED if entry["polls"] == 1 else LoginState.CONFIRMED
        if entry["state"] == LoginState.CONFIRMED:
            return LoginStatus(
                state=LoginState.CONFIRMED,
                account_id=entry["account_id"],
                display_name=FakePersonalAdapter.display_name,
                session=f"fake-session-{login_id}",
            )
        return LoginStatus(state=entry["state"])

    def send_text(self, channel, thread_id: str, text: str) -> AdapterSendResult:
        if FakePersonalAdapter._script:
            outcome = FakePersonalAdapter._script.pop(0)
            if outcome == "error":
                return AdapterSendResult(ok=False, error="fake send error")
            raise RestrictionDetected(outcome, "fake restriction")
        FakePersonalAdapter.sent.append((channel.pk, thread_id, text))
        return AdapterSendResult(ok=True, message_id=f"fake-{uuid.uuid4().hex[:12]}")

    def logout(self, channel) -> None:
        return None

    # --- test helpers ------------------------------------------------------------------

    @classmethod
    def expire(cls, login_id: str):
        cls.logins[login_id]["state"] = LoginState.EXPIRED

    @classmethod
    def fail_next(cls, *outcomes: str):
        cls._script.extend(outcomes)

    @classmethod
    def reset(cls):
        cls.logins.clear()
        cls.sent.clear()
        cls._script.clear()
        cls.account_id = "fake-zalo-account-1"
