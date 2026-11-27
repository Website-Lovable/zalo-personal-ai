"""Operator commands for live (real-data) exchange with AIVA (Phase 6). See
docs/PHASE6_AIVA_LIVE.md for the full runbook and rollback plan.

    python manage.py aiva_live status
    python manage.py aiva_live preflight [--tenant SLUG] [--check-health] [--json]
    python manage.py aiva_live activate --tenant SLUG --by NAME --approval-ref REF
    python manage.py aiva_live pause    --tenant SLUG --by NAME --reason TEXT
    python manage.py aiva_live resume   --tenant SLUG --by NAME
    python manage.py aiva_live revoke   --tenant SLUG --by NAME --reason TEXT
    python manage.py aiva_live pause-all  --by NAME --reason TEXT    # rollback level 2
    python manage.py aiva_live resume-all --by NAME

Every change is written to the audit log. ``preflight`` exits 1 when a check fails.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Tenant
from apps.integrations.aiva import config, live
from apps.integrations.aiva.models import AIVALiveEnrollment, IntegrationOutbox
from apps.integrations.aiva.preflight import run_preflight

ACTIONS = [
    "status",
    "preflight",
    "activate",
    "pause",
    "resume",
    "revoke",
    "pause-all",
    "resume-all",
]


class Command(BaseCommand):
    help = "Phase 6: preflight, activate, pause, resume and roll back live AIVA exchange."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=ACTIONS)
        parser.add_argument("--tenant", help="Tenant slug")
        parser.add_argument("--by", default="", help="Who runs this (name), for the audit log")
        parser.add_argument("--approval-ref", default="", help="Director's written approval")
        parser.add_argument("--reason", default="")
        parser.add_argument("--check-health", action="store_true", help="Call AIVA /health")
        parser.add_argument("--json", action="store_true", help="Machine-readable output")

    def handle(self, *args, **opts):
        live.record_mode()
        action = opts["action"]
        try:
            if action == "status":
                return self._status()
            if action == "preflight":
                return self._preflight(opts)
            if action in ("pause-all", "resume-all"):
                switch = live.set_global_pause(
                    action == "pause-all", by=self._by(opts), reason=opts["reason"]
                )
                state = "ĐANG DỪNG" if switch.paused else "đang mở"
                self.stdout.write(self.style.SUCCESS(f"Công tắc live toàn hệ thống: {state}."))
                return None
            tenant = self._tenant(opts)
            by = self._by(opts)
            if action == "activate":
                enrollment = live.activate(tenant, operator=by, approval_ref=opts["approval_ref"])
            elif action == "pause":
                enrollment = live.pause(tenant, by=by, reason=opts["reason"])
            elif action == "resume":
                enrollment = live.resume(tenant, operator=by)
            else:
                enrollment = live.revoke(tenant, by=by, reason=opts["reason"])
        except live.LiveStateError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"{tenant.slug}: {enrollment.get_status_display()}."))
        return None

    def _by(self, opts) -> str:
        by = (opts["by"] or "").strip()
        if not by:
            raise CommandError("--by NAME là bắt buộc (ghi vào nhật ký).")
        return by

    def _tenant(self, opts) -> Tenant:
        if not opts["tenant"]:
            raise CommandError("--tenant SLUG là bắt buộc.")
        tenant = Tenant.objects.filter(slug=opts["tenant"]).first()
        if tenant is None:
            raise CommandError(f"Không có tenant {opts['tenant']}.")
        return tenant

    def _status(self):
        switch = live.get_switch()
        self.stdout.write(
            f"Chế độ: {config.get_mode()} | live đã duyệt: {config.live_approved()} | "
            f"công tắc toàn hệ thống: {'DỪNG' if switch.paused else 'mở'}"
        )
        for enrollment in AIVALiveEnrollment.objects.select_related("tenant").order_by("tenant"):
            frozen = (
                IntegrationOutbox.objects.for_tenant(enrollment.tenant)
                .filter(data_origin="real", status__in=["pending", "failed"])
                .count()
            )
            self.stdout.write(
                f"- {enrollment.tenant.slug}: {enrollment.status}"
                f" | văn bản duyệt: {enrollment.approval_ref or '-'}"
                f" | sự kiện thật chờ gửi: {frozen}"
            )

    def _preflight(self, opts):
        tenant = self._tenant(opts) if opts["tenant"] else None
        report = run_preflight(tenant=tenant, check_health=opts["check_health"])
        if opts["json"]:
            self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            marks = {"ok": "OK  ", "warn": "CHÚ Ý", "fail": "LỖI "}
            for check in report.checks:
                count = f" ({check.count})" if check.count is not None else ""
                self.stdout.write(f"[{marks[check.level]}] {check.code}: {check.message}{count}")
            verdict = "ĐẠT" if report.passed else "CHƯA ĐẠT"
            self.stdout.write(f"Kết quả preflight: {verdict}")
        if not report.passed:
            raise SystemExit(1)
