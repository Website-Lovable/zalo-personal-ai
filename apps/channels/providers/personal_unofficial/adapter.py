"""Adapter interface between the core and the (to-be-approved) personal Zalo login library.

Nothing in this module talks to Zalo. ``SidecarAdapter`` forwards calls to a separate Node
process that wraps the login library approved by the project owner on 26/11/2026
(zca-js 2.4.0, MIT; Kickoff 3 #7). ``FakePersonalAdapter`` (``fake.py``) implements the same
interface for tests and local development.

Rules every adapter must follow:
- Login is by QR code only, scanned by the account owner on their own phone. No password,
  no cookie/IMEI import, no access to anyone else's account.
- The session is an opaque string; the core stores it encrypted (``Channel.session_data``)
  and never logs it.
- Signs that Zalo is restricting the account are raised as ``RestrictionDetected`` so the
  core can stop the channel at once.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings


class LoginState:
    PENDING = "pending"  # QR shown, not scanned yet
    SCANNED = "scanned"  # scanned, waiting for the owner to confirm on the phone
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    FAILED = "failed"

    FINAL = {CONFIRMED, EXPIRED, FAILED}


@dataclass(frozen=True)
class LoginChallenge:
    login_id: str
    qr_image: str  # data URL (image/png;base64,...) the owner scans with the Zalo app
    expires_at: datetime


@dataclass(frozen=True)
class LoginStatus:
    state: str
    account_id: str = ""
    display_name: str = ""
    session: str = ""  # opaque; only set when state == CONFIRMED
    error: str = ""


@dataclass(frozen=True)
class AdapterSendResult:
    ok: bool
    message_id: str = ""
    error: str = ""


class RestrictionDetected(Exception):
    """Zalo shows signs of restricting the account: stop the channel immediately.

    ``kind``: ``login_failed`` | ``session_expired`` | ``blocked`` | ``warning`` |
    ``account_changed`` | ``too_many_failures``.
    """

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class PersonalSessionAdapter(ABC):
    name: str = ""

    @abstractmethod
    def start_login(self, channel) -> LoginChallenge:
        """Create a QR login for this channel."""

    @abstractmethod
    def login_status(self, channel, login_id: str) -> LoginStatus:
        """Poll a QR login started with ``start_login``."""

    @abstractmethod
    def send_text(self, channel, thread_id: str, text: str) -> AdapterSendResult:
        """Send a 1-1 text message. Raise ``RestrictionDetected`` on restriction signs."""

    @abstractmethod
    def logout(self, channel) -> None:
        """End the session on the sidecar (best effort)."""


class SidecarError(Exception):
    """The sidecar could not be reached or answered with an unexpected response."""


class SidecarAdapter(PersonalSessionAdapter):
    """Talks to the Node sidecar (``sidecar/zalo-personal``, zca-js 2.4.0) over HTTP.

    Every request is signed like the sidecar's events: HMAC-SHA256 of
    ``"<timestamp>.<body>"`` with ``ZALO_PERSONAL_SIDECAR_SECRET``. The sidecar keeps live
    sessions in memory only; after a restart the adapter restores a session from the
    encrypted copy in ``Channel.session_data``.
    """

    name = "sidecar"

    def _request(self, method: str, path: str, payload: dict | None = None):
        import json
        import time

        import httpx

        from ..http import http_client
        from .provider import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

        base = settings.ZALO_PERSONAL_SIDECAR_URL.rstrip("/")
        secret = settings.ZALO_PERSONAL_SIDECAR_SECRET
        if not (base and secret):
            raise SidecarError("ZALO_PERSONAL_SIDECAR_URL / ZALO_PERSONAL_SIDECAR_SECRET not set")
        body = json.dumps(payload or {}, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(secret, timestamp, body),
        }
        try:
            with http_client() as client:
                response = client.request(method, f"{base}{path}", content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise SidecarError(f"sidecar unreachable: {exc.__class__.__name__}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise SidecarError(f"sidecar returned HTTP {response.status_code}") from exc
        return response.status_code, data if isinstance(data, dict) else {}

    @staticmethod
    def _key(channel) -> str:
        return str(channel.webhook_key)

    def start_login(self, channel) -> LoginChallenge:
        status, data = self._request("POST", f"/v1/sessions/{self._key(channel)}/login")
        if status != 200 or not data.get("login_id"):
            raise SidecarError(f"login start failed: {data.get('error', status)}")
        return LoginChallenge(
            login_id=str(data["login_id"]),
            qr_image=str(data.get("qr_image", "")),
            expires_at=datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00")),
        )

    def login_status(self, channel, login_id: str) -> LoginStatus:
        status, data = self._request(
            "POST", f"/v1/sessions/{self._key(channel)}/login/{login_id}/status"
        )
        if status == 404:
            return LoginStatus(state=LoginState.EXPIRED)
        if status != 200:
            raise SidecarError(f"login status failed: {data.get('error', status)}")
        return LoginStatus(
            state=str(data.get("state", LoginState.FAILED)),
            account_id=str(data.get("account_id", "")),
            display_name=str(data.get("display_name", "")),
            session=str(data.get("session", "")),
            error=str(data.get("error", "")),
        )

    def _restore(self, channel):
        if not channel.session_data:
            raise RestrictionDetected("session_expired", "no stored session")
        status, data = self._request(
            "POST",
            f"/v1/sessions/{self._key(channel)}/restore",
            {"session": channel.session_data},
        )
        if status != 200 or not data.get("ok"):
            raise RestrictionDetected(
                str(data.get("restriction") or "session_expired"), str(data.get("error", ""))
            )
        known = (channel.config or {}).get("zalo_account_id")
        if known and data.get("account_id") and str(data["account_id"]) != known:
            raise RestrictionDetected("account_changed", "restored session is another account")

    def send_text(self, channel, thread_id: str, text: str) -> AdapterSendResult:
        path = f"/v1/sessions/{self._key(channel)}/send"
        payload = {"thread_id": thread_id, "text": text}
        status, data = self._request("POST", path, payload)
        if status == 409 and data.get("error") == "no_session":
            self._restore(channel)
            status, data = self._request("POST", path, payload)
        if data.get("restriction"):
            raise RestrictionDetected(str(data["restriction"]), str(data.get("error", "")))
        if status == 200 and data.get("ok"):
            return AdapterSendResult(ok=True, message_id=str(data.get("message_id", "")))
        return AdapterSendResult(ok=False, error=str(data.get("error") or f"HTTP {status}"))

    def logout(self, channel) -> None:
        self._request("DELETE", f"/v1/sessions/{self._key(channel)}")


def get_adapter() -> PersonalSessionAdapter:
    """Mock backend (dev/test) always uses the fake; real backend uses the sidecar."""
    if settings.ZALO_CHANNEL_BACKEND == "mock":
        from .fake import FakePersonalAdapter

        return FakePersonalAdapter()
    return SidecarAdapter()
