"""Load test (Phase 5, Blueprint D5). Runs against a local or staging stack seeded with
`python manage.py seed_demo --password <pw>`; never against production with real customers.

Two kinds of virtual users:
- DashboardUser: a shop owner on the mobile admin UI (login, stats, inbox, approvals).
- ZaloWebhookUser: Zalo pushing customer messages to /webhooks/zalo-oa/ for the demo OA
  (signed like Zalo does). Needs the same ZALO_OA_APP_ID / ZALO_OA_WEBHOOK_SECRET as the
  target, and ZALO_CHANNEL_BACKEND=mock + LLM_PROVIDER=mock there so nothing leaves it.

    pip install -r loadtest/requirements.txt
    LOADTEST_PASSWORD=... locust -f loadtest/locustfile.py --host http://localhost:8000 \
        --headless -u 50 -r 10 -t 2m --csv loadtest/results/run

See docs/PHASE5_TESTING_DEPLOYMENT.md for the targets and the last measured numbers.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import random
import time
import uuid
from urllib.parse import urlparse

from locust import HttpUser, between, events, task

USERNAME = os.environ.get("LOADTEST_USERNAME", "demo_owner")
PASSWORD = os.environ.get("LOADTEST_PASSWORD", "")
APP_ID = os.environ.get("ZALO_OA_APP_ID", "loadtest-app")
OA_SECRET = os.environ.get("ZALO_OA_WEBHOOK_SECRET", "loadtest-oa-secret")
OA_ID = os.environ.get("LOADTEST_OA_ID", "demo-oa")
# Number of distinct fake Zalo customers the webhook users rotate through.
CUSTOMERS = int(os.environ.get("LOADTEST_CUSTOMERS", "200"))
# Acceptance thresholds (docs/PHASE5_TESTING_DEPLOYMENT.md); the run exits 1 when missed.
MAX_FAIL_RATIO = float(os.environ.get("LOADTEST_MAX_FAIL_RATIO", "0.01"))
MAX_P95_MS = float(os.environ.get("LOADTEST_MAX_P95_MS", "800"))
# Only for a local rehearsal behind Caddy's internal (self-signed) CA.
INSECURE = os.environ.get("LOADTEST_INSECURE") == "1"

_seq = itertools.count()
TEXTS = [
    "Chào shop, hôm nay có mở cửa không?",
    "Shop ơi còn hàng không ạ?",
    "Cho mình hỏi địa chỉ cửa hàng",
    "Sản phẩm này giá bao nhiêu vậy?",  # sensitive -> draft for approval
    "Cảm ơn shop nhé",
]


@events.test_start.add_listener
def _refuse_unsafe_targets(environment, **kwargs):
    host = urlparse(environment.host or "").hostname or ""
    allowed = {"localhost", "127.0.0.1", "web", "::1"} | set(
        filter(None, os.environ.get("LOADTEST_ALLOWED_HOSTS", "").split(","))
    )
    if host not in allowed:
        raise SystemExit(
            f"Refusing to load-test {host!r}. Add it to LOADTEST_ALLOWED_HOSTS if it is a "
            "staging stack with demo data only."
        )


@events.quitting.add_listener
def _check_thresholds(environment, **kwargs):
    total = environment.stats.total
    if total.num_requests == 0:
        return
    p95 = total.get_response_time_percentile(0.95)
    ok = total.fail_ratio <= MAX_FAIL_RATIO and p95 <= MAX_P95_MS
    print(
        f"Thresholds: fail_ratio={total.fail_ratio:.4f} (max {MAX_FAIL_RATIO}), "
        f"p95={p95:.0f} ms (max {MAX_P95_MS:.0f}) -> {'PASS' if ok else 'FAIL'}"
    )
    if not ok:
        environment.process_exit_code = 1


def oa_signature(body: bytes, timestamp: str) -> str:
    # Same formula as apps.channels.providers.zalo_oa.compute_signature
    raw = APP_ID.encode() + body + timestamp.encode() + OA_SECRET.encode()
    return "mac=" + hashlib.sha256(raw).hexdigest()


class DashboardUser(HttpUser):
    weight = 3
    wait_time = between(1, 3)

    def on_start(self):
        self.client.verify = not INSECURE
        if not PASSWORD:
            raise SystemExit("Set LOADTEST_PASSWORD (the seed_demo password).")
        response = self.client.post(
            "/api/auth/login/", json={"username": USERNAME, "password": PASSWORD}
        )
        response.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {response.json()['access']}"
        self.conversation_ids: list[int] = []

    @task(5)
    def stats(self):
        self.client.get("/api/dashboard/stats/")

    @task(3)
    def conversations(self):
        response = self.client.get("/api/conversations/")
        if response.ok:
            self.conversation_ids = [c["id"] for c in response.json().get("results", [])]

    @task(3)
    def messages(self):
        if self.conversation_ids:
            cid = random.choice(self.conversation_ids)  # noqa: S311
            self.client.get(
                f"/api/conversations/{cid}/messages/", name="/conversations/:id/messages/"
            )

    @task(2)
    def approvals(self):
        self.client.get("/api/approvals/?status=pending")

    @task(1)
    def contacts(self):
        self.client.get("/api/contacts/")

    @task(1)
    def analytics(self):
        self.client.get("/api/dashboard/analytics/?days=7")

    @task(1)
    def activity(self):
        self.client.get("/api/dashboard/activity/")


class ZaloWebhookUser(HttpUser):
    weight = 1
    wait_time = between(0.5, 2)

    def on_start(self):
        self.client.verify = not INSECURE

    @task
    def customer_message(self):
        n = next(_seq)
        timestamp = str(int(time.time() * 1000))
        payload = {
            "app_id": APP_ID,
            "event_name": "user_send_text",
            "timestamp": timestamp,
            "sender": {"id": f"lt-user-{n % CUSTOMERS}"},
            "recipient": {"id": OA_ID},
            "message": {"msg_id": f"lt-{uuid.uuid4().hex}", "text": random.choice(TEXTS)},  # noqa: S311
        }
        body = json.dumps(payload).encode()
        self.client.post(
            "/webhooks/zalo-oa/",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-ZEvent-Signature": oa_signature(body, timestamp),
            },
            name="/webhooks/zalo-oa/",
        )
