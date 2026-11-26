"""End-to-end sandbox run of the AIVA integration with demo data (Phase 3.5 acceptance).

    AIVA_INTEGRATION_MODE=sandbox python manage.py aiva_sandbox_run

What it does, all on the demo tenant (data_origin=demo), without any real AIVA or Zalo:
1. plays AIVA calling Zalo AI: health, contacts/upsert, outreach/draft, conversation summary,
   each signed with HMAC exactly as AIVA must sign;
2. plays a demo customer writing in, so the AI pipeline (mock LLM) and memory produce events;
3. approves the outreach draft as the staff in charge (mock Zalo channel only);
4. delivers the outbox to a fake AIVA that checks every signature and the schema v1;
5. prints a report. Refuses to run in any mode other than sandbox.

Secrets that are not configured are replaced by throwaway values for this run only.
"""

from __future__ import annotations

import json
import secrets
import uuid
from contextlib import contextmanager

import httpx
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.test import Client, override_settings

from apps.accounts.models import Tenant
from apps.ai_engine.pipeline import process_inbound_message
from apps.channels.models import Channel, ChannelType
from apps.channels.providers.base import ChannelEvent, InboundMessage
from apps.conversations.inbound import handle_channel_event
from apps.integrations.aiva import config, delivery, outbox
from apps.integrations.aiva.client import HTTPAIVAClient
from apps.integrations.aiva.models import IntegrationOutbox
from apps.integrations.aiva.schema import validate_envelope
from apps.integrations.aiva.signing import sign_request_headers, verify_signature
from apps.memory.services import update_memory
from apps.permissions import services as approvals
from apps.permissions.models import ApprovalQueue

DEMO_SLUG = "aiva-demo"


@contextmanager
def _scripted_llm():
    """Mock LLM with a scripted answer, so the run shows intents, summary and memory."""
    from apps.ai_engine import pipeline
    from apps.ai_engine.llm import MockProvider
    from apps.memory import services as memory

    main = MockProvider(
        model="mock-sandbox",
        canned_reply="Dạ em gửi anh/chị thông tin khóa học, tư vấn viên sẽ liên hệ ngay ạ.",
        canned_json={"detected_intents": ["price", "course_interest"], "confidence": 0.8},
    )
    fast = MockProvider(
        model="mock-sandbox-fast",
        canned_json={
            "summary": "Khách hỏi giá khóa AI cho 20 nhân viên và muốn gặp tư vấn viên.",
            "key_points": ["20 nhân viên", "muốn gặp tư vấn viên"],
            "facts": [
                {
                    "content": "Công ty có 20 nhân viên kinh doanh",
                    "memory_type": "fact",
                    "importance": 8,
                }
            ],
        },
    )
    originals = (pipeline.get_llm_provider, memory.get_llm_provider)

    def factory(kind="main"):
        return fast if kind == "fast" else main

    pipeline.get_llm_provider = memory.get_llm_provider = factory
    try:
        yield
    finally:
        pipeline.get_llm_provider, memory.get_llm_provider = originals


class FakeAIVA:
    """Receives webhooks like AIVA would: rejects bad signatures or schema."""

    def __init__(self, secret: str):
        self.secret = secret
        self.received: list[dict] = []
        self.rejected = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        try:
            verify_signature(
                self.secret,
                request.content,
                request.headers.get("X-Signature"),
                request.headers.get("X-Timestamp"),
            )
            envelope = json.loads(request.content)
            validate_envelope(envelope)
        except Exception as exc:  # noqa: BLE001
            self.rejected += 1
            return httpx.Response(400, text=str(exc))
        if envelope["data_origin"] != "demo":
            self.rejected += 1
            return httpx.Response(422, text="sandbox accepts demo data only")
        self.received.append(envelope)
        return httpx.Response(202)


class Command(BaseCommand):
    help = "Chạy thử tích hợp AIVA ở chế độ sandbox với dữ liệu demo (không gọi ra ngoài)."

    def handle(self, *args, **options):
        if config.get_mode() != config.SANDBOX:
            raise CommandError("Chỉ chạy khi AIVA_INTEGRATION_MODE=sandbox.")
        overrides = {
            "AIVA_SERVICE_TOKEN": settings.AIVA_SERVICE_TOKEN or secrets.token_urlsafe(24),
            "AIVA_INBOUND_SECRET": settings.AIVA_INBOUND_SECRET or secrets.token_urlsafe(24),
            "AIVA_WEBHOOK_SECRET": settings.AIVA_WEBHOOK_SECRET or secrets.token_urlsafe(24),
            "ALLOWED_HOSTS": [*settings.ALLOWED_HOSTS, "testserver"],
            "ZALO_CHANNEL_BACKEND": "mock",
            "AI_ENGINE_ENABLED": True,
            "LLM_PROVIDER": "mock",
        }
        from config.celery import app as celery_app

        eager = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = True  # run the AI pipeline inline
        original_schedule = outbox._schedule_delivery
        outbox._schedule_delivery = lambda item_id: None  # deliver below, to the fake AIVA
        try:
            with override_settings(**overrides):
                self._run()
        finally:
            outbox._schedule_delivery = original_schedule
            celery_app.conf.task_always_eager = eager

    # --- steps -------------------------------------------------------------------------

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        full = f"/api/integration/v1/{path}"
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
        headers = sign_request_headers(
            settings.AIVA_INBOUND_SECRET, method, full, self.tenant.external_ref, raw
        )
        extra = {
            "HTTP_AUTHORIZATION": f"Bearer {settings.AIVA_SERVICE_TOKEN}",
            "HTTP_X_TENANT_REF": self.tenant.external_ref,
            "HTTP_X_SIGNATURE": headers["X-Signature"],
            "HTTP_X_TIMESTAMP": headers["X-Timestamp"],
            "HTTP_IDEMPOTENCY_KEY": f"sandbox-{uuid.uuid4()}",
        }
        client = Client()
        if method == "GET":
            response = client.get(full, **extra)
        else:
            response = client.generic(method, full, raw, content_type="application/json", **extra)
        data = response.json()
        self.stdout.write(
            f"  {method:4} {path:40} -> {response.status_code} {data.get('code', '')}"
        )
        return response.status_code, data

    def _run(self):
        if not Tenant.objects.filter(slug=DEMO_SLUG).exists():
            call_command("seed_demo", stdout=self.stdout)
        self.tenant = Tenant.objects.get(slug=DEMO_SLUG)
        oa = Channel.objects.get(
            tenant=self.tenant, channel_type=ChannelType.ZALO_OA, data_origin="demo"
        )
        before = set(IntegrationOutbox.objects.values_list("pk", flat=True))
        run_id = uuid.uuid4().hex[:6]

        self.stdout.write(self.style.MIGRATE_HEADING("1) AIVA gọi Zalo AI (ký HMAC)"))
        self._call("GET", "health")
        status, upserted = self._call(
            "POST",
            "contacts/upsert",
            {
                "data_origin": "demo",
                "crs_customer_id": f"CRS-SBX-{run_id}",
                "display_name": f"Khách sandbox {run_id} (demo)",
                "segment": "b2b",
                "owner_staff_ref": "staff-demo-01",
                "consent_status": "granted",
                "consent_at": "2026-11-26T09:00:00+07:00",
                "channel_ref": str(oa.pk),
                "zalo_user_id": f"sbx-{run_id}",
            },
        )
        self._call(
            "POST",
            "contacts/upsert",
            {
                "data_origin": "real",
                "crs_customer_id": "CRS-REAL-1",
                "display_name": "x",
                "consent_status": "granted",
                "consent_at": "2026-11-26T09:00:00+07:00",
            },
        )  # must be refused: sandbox never takes real data
        _, draft = self._call(
            "POST",
            "outreach/draft",
            {
                "data_origin": "demo",
                "crs_customer_id": f"CRS-SBX-{run_id}",
                "draft_text": "Chào anh/chị, bên em vừa mở lớp AI cho đội kinh doanh tháng 12 ạ.",
                "purpose": "care_followup",
            },
        )

        self.stdout.write(self.style.MIGRATE_HEADING("2) Khách demo nhắn tin → AI xử lý"))
        contact_id = upserted["contact_ref"]
        event = ChannelEvent(
            kind="message",
            event_name="user_send_text",
            dedupe_key=f"sbx-msg-{run_id}",
            user_id=f"sbx-{run_id}",
            message=InboundMessage(
                sender_id=f"sbx-{run_id}",
                text="Cho em xin giá khóa AI cho 20 người, nhờ nhân viên tư vấn gọi lại cho em.",
                message_id=f"sbx-msg-{run_id}",
                sender_name="Khách sandbox",
            ),
        )
        if oa.status != Channel.Status.ACTIVE:  # demo channel on the mock Zalo backend
            oa.status = Channel.Status.ACTIVE
            oa.save(update_fields=["status", "updated_at"])
        with _scripted_llm():
            handle_channel_event(oa, event)
        conversation = oa.conversations.get(contact_id=contact_id)
        inbound = conversation.messages.filter(direction="inbound").latest("id")
        with _scripted_llm():
            # Normally started on commit by the inbound signal; idempotent if it already ran.
            process_inbound_message(inbound.pk)
        with _scripted_llm():
            update_memory(conversation, force=True)
        self._call("GET", f"conversations/{conversation.pk}/summary")

        self.stdout.write(self.style.MIGRATE_HEADING("3) Nhân viên phụ trách duyệt tin chăm sóc"))
        approval = ApprovalQueue.objects.get(pk=draft["approval_ref"])
        owner = self.tenant.users.filter(role="owner").first()
        approval = approvals.approve(approval, owner)
        self.stdout.write(f"  duyệt #{approval.pk}: {approval.status}, đã gửi qua kênh mock")

        self.stdout.write(self.style.MIGRATE_HEADING("4) Giao outbox tới AIVA giả lập"))
        fake = FakeAIVA(settings.AIVA_WEBHOOK_SECRET)
        client = HTTPAIVAClient(
            base_url="https://aiva-sandbox.invalid",
            secret=settings.AIVA_WEBHOOK_SECRET,
            events_path=settings.AIVA_EVENTS_PATH,
            transport=httpx.MockTransport(fake),
        )
        counts = delivery.deliver_due(client=client, limit=500)
        new = IntegrationOutbox.objects.exclude(pk__in=before).order_by("id")
        for item in new:
            self.stdout.write(f"  {item.event_type:26} {item.status:6} attempts={item.attempts}")

        self.stdout.write(self.style.MIGRATE_HEADING("Kết quả"))
        self.stdout.write(
            f"  outbox: {counts}; AIVA giả lập nhận {len(fake.received)} sự kiện hợp lệ, "
            f"từ chối {fake.rejected}"
        )
        ok = (
            status == 201
            and counts["failed"] == counts["dead"] == 0
            and fake.rejected == 0
            and len(fake.received) >= 3
            and all(e["data_origin"] == "demo" for e in fake.received)
        )
        if not ok:
            raise CommandError("Sandbox run không đạt.")
        self.stdout.write(self.style.SUCCESS("Sandbox AIVA: ĐẠT (chỉ dữ liệu demo)."))
