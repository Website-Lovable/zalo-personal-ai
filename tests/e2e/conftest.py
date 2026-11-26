"""End-to-end fixtures: the whole stack over HTTP with the in-process fake Zalo and the
MockProvider LLM. Nothing leaves the process (see the root conftest network guard)."""

import json

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.channels.providers.http import use_transport
from apps.channels.testing.fake_zalo import FakeZalo


@pytest.fixture
def fake_zalo():
    fake = FakeZalo()
    with override_settings(ZALO_CHANNEL_BACKEND="real"), use_transport(fake.transport):
        yield fake


class Stack:
    """Drives the app the way users and Zalo do: public HTTP endpoints only."""

    def __init__(self, fake, capture):
        self.fake = fake
        self.capture = capture
        self.public = APIClient()

    def signup(self, tenant_name: str, username: str, password: str = "E2e-pass-2026!"):
        response = self.public.post(
            "/api/auth/register/",
            {"tenant_name": tenant_name, "username": username, "password": password},
            format="json",
        )
        assert response.status_code == 201, response.content
        login = self.public.post(
            "/api/auth/login/", {"username": username, "password": password}, format="json"
        )
        assert login.status_code == 200, login.content
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
        return client

    def connect_oa(self, client, name="OA cửa hàng"):
        """Create an OA channel and run the OAuth v4 + PKCE flow against the fake Zalo."""
        from urllib.parse import parse_qs, urlparse

        created = client.post(
            "/api/channels/",
            {"channel_type": "zalo_oa", "name": name, "mode": "auto"},
            format="json",
        )
        assert created.status_code == 201, created.content
        channel_id = created.json()["id"]
        start = client.post(f"/api/channels/{channel_id}/oauth/start/")
        assert start.status_code == 200, start.content
        query = {
            k: v[0] for k, v in parse_qs(urlparse(start.json()["authorize_url"]).query).items()
        }
        code = self.fake.issue_code(query["code_challenge"])
        callback = self.public.get(
            "/oauth/zalo-oa/callback/",
            {"code": code, "oa_id": self.fake.oa_id, "state": query["state"]},
        )
        assert callback.status_code == 200, callback.content
        return channel_id

    def zalo_sends(self, user_id: str, text: str, msg_id: str, *, path="/webhooks/zalo-oa/"):
        """Zalo delivers a user's text message to our webhook (signed like the real one)."""
        payload = FakeZalo.oa_event(
            "user_send_text",
            sender={"id": user_id},
            recipient={"id": self.fake.oa_id},
            message={"msg_id": msg_id, "text": text},
        )
        body = json.dumps(payload).encode()
        with self.capture(execute=True):
            response = self.public.post(
                path,
                data=body,
                content_type="application/json",
                HTTP_X_ZEVENT_SIGNATURE=FakeZalo.sign(body),
            )
        assert response.status_code == 200, response.content
        return response

    def sent_to(self, user_id: str) -> list[str]:
        return [m["text"] for m in self.fake.oa_sent if m["user_id"] == user_id]


@pytest.fixture
def stack(db, fake_zalo, django_capture_on_commit_callbacks, settings):
    settings.DEFAULT_AI_RESPONSE_DELAY_SECONDS = 0
    return Stack(fake_zalo, django_capture_on_commit_callbacks)
