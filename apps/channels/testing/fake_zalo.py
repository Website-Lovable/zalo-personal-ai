"""In-process fake of the Zalo OA / OAuth v4 / Bot Platform HTTP APIs.

Used by the test suite (and usable in a shell for demos) through ``httpx.MockTransport``::

    fake = FakeZalo()
    with use_transport(fake.transport):
        ...  # every Zalo HTTP call now hits the fake

It follows the request/response shapes of the official docs closely enough to exercise the
real providers end to end: PKCE checks, single-use refresh tokens, token expiry errors,
webhook/getUpdates exclusivity and per-message ids.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from django.conf import settings

from apps.channels.providers.zalo_oa import compute_signature


def _host(url: str) -> str:
    return urlparse(url).netloc


@dataclass
class FakeZalo:
    oa_id: str = "1234567890123456789"
    access_ttl: int = 90000
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    oa_sent: list[dict[str, Any]] = field(default_factory=list)
    bot_sent: list[dict[str, Any]] = field(default_factory=list)
    bot_updates: list[dict[str, Any]] = field(default_factory=list)
    bot_tokens: set[str] = field(default_factory=lambda: {"111:valid-bot-token"})
    bot_webhook: dict[str, str] = field(default_factory=dict)
    #: Next N OA API calls fail with this error code (e.g. -216 expired token).
    oa_fail_codes: list[int] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def __post_init__(self):
        self._codes: dict[str, str] = {}  # code -> code_challenge
        self._access_tokens: set[str] = set()
        self._refresh_tokens: set[str] = set()

    # -- helpers for tests ----------------------------------------------------------------

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def issue_code(self, code_challenge: str) -> str:
        code = secrets.token_hex(8)
        self._codes[code] = code_challenge
        return code

    def issue_tokens(self) -> tuple[str, str]:
        access, refresh = f"at-{secrets.token_hex(6)}", f"rt-{secrets.token_hex(6)}"
        self._access_tokens.add(access)
        self._refresh_tokens.add(refresh)
        return access, refresh

    def expire_access_tokens(self):
        self._access_tokens.clear()

    def add_user(self, user_id: str, name: str, *, follower: bool = True):
        self.users[user_id] = {
            "user_id": user_id,
            "display_name": name,
            "avatar": f"https://s120.avatar.talk.zdn.vn/{user_id}.jpg",
            "user_is_follower": follower,
        }

    @staticmethod
    def oa_event(event_name: str, **fields) -> dict[str, Any]:
        payload = {
            "app_id": settings.ZALO_OA_APP_ID,
            "event_name": event_name,
            "timestamp": fields.pop("timestamp", "1764163200000"),
        }
        payload.update(fields)
        return payload

    @staticmethod
    def sign(body: bytes, secret: str | None = None) -> str:
        payload = json.loads(body)
        digest = compute_signature(
            str(payload.get("app_id", "")),
            body,
            str(payload.get("timestamp", "")),
            secret if secret is not None else settings.ZALO_OA_WEBHOOK_SECRET,
        )
        return f"mac={digest}"

    # -- request routing ------------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == _host(settings.ZALO_OA_OAUTH_URL):
            return self._oauth(request)
        if host == _host(settings.ZALO_OA_API_URL):
            return self._oa_api(request)
        if host == _host(settings.ZALO_BOT_API_URL):
            return self._bot(request)
        return httpx.Response(404, json={"error": "unknown host"})

    def _oauth(self, request):
        if request.headers.get("secret_key") != settings.ZALO_OA_APP_SECRET:
            return httpx.Response(200, json={"error": -14002, "error_name": "Invalid secret"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("app_id") != settings.ZALO_OA_APP_ID:
            return httpx.Response(200, json={"error": -14001, "error_name": "Invalid app_id"})
        if form.get("grant_type") == "authorization_code":
            challenge = self._codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            digest = hashlib.sha256(verifier.encode()).digest()
            expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if challenge is None or challenge != expected:
                return httpx.Response(
                    200, json={"error": -14019, "error_description": "Invalid code"}
                )
        elif form.get("grant_type") == "refresh_token":
            token = form.get("refresh_token", "")
            if token not in self._refresh_tokens:
                return httpx.Response(
                    200, json={"error": -14014, "error_description": "Invalid refresh token"}
                )
            self._refresh_tokens.discard(token)  # single use
        else:
            return httpx.Response(200, json={"error": -14003, "error_name": "Bad grant"})
        access, refresh = self.issue_tokens()
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": str(self.access_ttl),
            },
        )

    def _oa_api(self, request):
        if self.oa_fail_codes:
            code = self.oa_fail_codes.pop(0)
            return httpx.Response(200, json={"error": code, "message": f"fake error {code}"})
        if request.headers.get("access_token") not in self._access_tokens:
            return httpx.Response(200, json={"error": -216, "message": "Access token is invalid"})
        path = request.url.path
        if path == "/v3.0/oa/message/cs":
            body = json.loads(request.content)
            user_id = body["recipient"]["user_id"]
            if user_id not in self.users:
                return httpx.Response(200, json={"error": -213, "message": "User not follow OA"})
            message_id = f"oa-msg-{len(self.oa_sent) + 1}"
            self.oa_sent.append({"user_id": user_id, "text": body["message"]["text"]})
            return httpx.Response(
                200,
                json={
                    "data": {"message_id": message_id, "user_id": user_id},
                    "error": 0,
                    "message": "Success",
                },
            )
        query = json.loads(request.url.params.get("data", "{}"))
        if path == "/v3.0/oa/user/detail":
            user = self.users.get(str(query.get("user_id")))
            if user is None:
                return httpx.Response(200, json={"error": -201, "message": "User not found"})
            return httpx.Response(200, json={"data": user, "error": 0, "message": "Success"})
        if path == "/v3.0/oa/user/getlist":
            ids = sorted(self.users)
            offset, count = int(query.get("offset", 0)), int(query.get("count", 15))
            page = [{"user_id": uid} for uid in ids[offset : offset + count]]
            data = {"total": len(ids), "count": len(page), "offset": offset, "users": page}
            return httpx.Response(200, json={"data": data, "error": 0, "message": "Success"})
        return httpx.Response(200, json={"error": -1, "message": "unknown endpoint"})

    def _bot(self, request):
        _, token_part, method = request.url.path.split("/", 2)
        token = token_part.removeprefix("bot")
        if token not in self.bot_tokens:
            return httpx.Response(
                401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
            )
        body = json.loads(request.content or b"{}")

        def ok(result):
            return httpx.Response(200, json={"ok": True, "result": result})

        if method == "getMe":
            return ok(
                {
                    "id": "9988",
                    "account_name": "bot.test",
                    "account_type": "BASIC",
                    "can_join_groups": False,
                }
            )
        if method == "setWebhook":
            secret = body.get("secret_token", "")
            if not 8 <= len(secret) <= 256 or not str(body.get("url", "")).startswith("https://"):
                return httpx.Response(
                    400, json={"ok": False, "error_code": 400, "description": "Bad request"}
                )
            self.bot_webhook = {"url": body["url"], "secret_token": secret}
            return ok({"url": body["url"], "updated_at": 1749538250568})
        if method == "deleteWebhook":
            self.bot_webhook = {}
            return ok({"url": "", "updated_at": 1749538250568})
        if method == "getWebhookInfo":
            return ok({"url": self.bot_webhook.get("url", ""), "updated_at": 1749633372026})
        if method == "getUpdates":
            if self.bot_webhook:
                return httpx.Response(
                    409,
                    json={"ok": False, "error_code": 409, "description": "Webhook is active"},
                )
            updates, self.bot_updates = self.bot_updates, []
            return ok(updates)
        if method == "sendMessage":
            message_id = f"bot-msg-{len(self.bot_sent) + 1}"
            self.bot_sent.append({"chat_id": body.get("chat_id"), "text": body.get("text")})
            return ok({"message_id": message_id, "date": 1749632637199})
        return httpx.Response(
            404, json={"ok": False, "error_code": 404, "description": "Not found"}
        )

    @staticmethod
    def bot_update(chat_id: str, text: str, *, message_id: str, name: str = "Khách") -> dict:
        return {
            "ok": True,
            "result": {
                "message": {
                    "from": {"id": chat_id, "display_name": name, "is_bot": False},
                    "chat": {"id": chat_id, "chat_type": "PRIVATE"},
                    "text": text,
                    "message_id": message_id,
                    "date": 1750316131602,
                },
                "event_name": "message.text.received",
            },
        }
