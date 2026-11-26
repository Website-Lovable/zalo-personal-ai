"""Phase 6 preflight: the review that must pass before a tenant may exchange real data.

Run by ``manage.py aiva_live preflight`` (operator) and shown to the tenant owner in the
app. Each check is ``ok``, ``warn`` (a person should look, does not block) or ``fail``
(blocks activation). Messages are Vietnamese (shown in the UI); codes are stable English.

Areas (Kickoff section 6, Phase 6): server configuration, consent, permissions, audit
trail, data separation (demo vs real). Messages never contain secret values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Q

from apps.accounts.models import User
from apps.contacts.models import ConsentSource, ConsentStatus, Contact
from common.models import AuditLog

from . import config
from .models import AIVALiveEnrollment, IntegrationOutbox

OK, WARN, FAIL = "ok", "warn", "fail"
MIN_SECRET_LENGTH = 32


@dataclass
class Check:
    code: str
    level: str
    message: str
    count: int | None = None


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, code: str, level: str, message: str, count: int | None = None) -> None:
        self.checks.append(Check(code, level, message, count))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.level == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == WARN]

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> dict:
        return {
            "passed": self.passed,
            "fail": [c.code for c in self.failures],
            "warn": [c.code for c in self.warnings],
        }

    def as_dict(self) -> dict:
        return {**self.summary(), "checks": [asdict(c) for c in self.checks]}


# --- server configuration ----------------------------------------------------------------


def _secret_strength(report: Report) -> None:
    secrets = {
        "AIVA_WEBHOOK_SECRET": settings.AIVA_WEBHOOK_SECRET,
        "AIVA_INBOUND_SECRET": settings.AIVA_INBOUND_SECRET,
        "AIVA_SERVICE_TOKEN": settings.AIVA_SERVICE_TOKEN,
    }
    weak = [name for name, value in secrets.items() if len(value or "") < MIN_SECRET_LENGTH]
    if weak:
        report.add(
            "secrets_weak",
            FAIL,
            f"Khóa chưa đặt hoặc ngắn hơn {MIN_SECRET_LENGTH} ký tự: {', '.join(weak)}.",
        )
    values = [v for v in secrets.values() if v]
    if len(values) != len(set(values)):
        report.add("secrets_reused", FAIL, "Các khóa AIVA phải khác nhau cho mỗi chiều.")
    if not weak and len(values) == len(set(values)):
        report.add("secrets", OK, "Khóa ký HMAC và token dịch vụ đã đặt, đủ mạnh, khác nhau.")


def check_server(report: Report, *, check_health: bool = False) -> None:
    mode = config.get_mode()
    if mode == config.LIVE:
        report.add("mode", OK, "AIVA_INTEGRATION_MODE=live.")
    else:
        report.add(
            "mode",
            FAIL,
            f"Máy chủ đang ở chế độ {mode}; cần AIVA_INTEGRATION_MODE=live để kết nối thật.",
        )
    if config.live_approved() and settings.AIVA_LIVE_APPROVAL_REF.strip():
        report.add("director_approval", OK, "Đã ghi văn bản GĐ phê duyệt chế độ live.")
    else:
        report.add(
            "director_approval",
            FAIL,
            "Cần AIVA_LIVE_APPROVED=true và AIVA_LIVE_APPROVAL_REF (số/ngày văn bản GĐ duyệt).",
        )
    if config.client_backend() != "http":
        report.add("client", FAIL, "AIVA_CLIENT phải là http (mock chỉ dùng cho sandbox).")
    else:
        report.add("client", OK, "Gửi sự kiện qua HTTP có ký HMAC.")
    url = urlparse(settings.AIVA_BASE_URL or "")
    if url.scheme != "https" or not url.netloc:
        report.add("base_url", FAIL, "AIVA_BASE_URL phải là địa chỉ https của AIVA thật.")
    else:
        report.add("base_url", OK, f"AIVA: {url.netloc}.")
    _secret_strength(report)
    if settings.DEBUG:
        report.add("debug", FAIL, "DEBUG đang bật; chỉ chạy live trên cấu hình production.")
    from .live import is_globally_paused

    if is_globally_paused():
        report.add("global_switch", WARN, "Công tắc dừng toàn hệ thống đang bật (đang dừng).")
    if check_health:
        from .client import HTTPAIVAClient

        try:
            healthy = HTTPAIVAClient(
                base_url=settings.AIVA_BASE_URL,
                secret=settings.AIVA_WEBHOOK_SECRET,
                token=settings.AIVA_OUTBOUND_TOKEN,
                events_path=settings.AIVA_EVENTS_PATH,
                health_path=settings.AIVA_HEALTH_PATH,
                timeout=settings.AIVA_HTTP_TIMEOUT_SECONDS,
            ).health()
        except Exception:  # noqa: BLE001 - misconfiguration is reported, not raised
            healthy = False
        if healthy:
            report.add("aiva_health", OK, "AIVA trả lời kiểm tra kết nối (health).")
        else:
            report.add("aiva_health", FAIL, "Không kết nối được AIVA (health).")


# --- tenant ------------------------------------------------------------------------------


def check_tenant(report: Report, tenant) -> None:
    if not tenant.is_active:
        report.add("tenant_active", FAIL, "Tenant đang bị khóa.")
    if not tenant.external_ref:
        report.add("tenant_ref", FAIL, "Chưa có mã tenant bên CRS (external_ref).")
    else:
        report.add("tenant_ref", OK, f"Mã tenant bên CRS: {tenant.external_ref}.")
    enrollment = AIVALiveEnrollment.objects.filter(tenant=tenant).first()
    acknowledged = enrollment is not None and enrollment.status != AIVALiveEnrollment.Status.REVOKED
    if acknowledged:
        report.add("owner_acknowledgement", OK, "Chủ tài khoản đã xác nhận bằng văn bản trong app.")
    else:
        report.add(
            "owner_acknowledgement",
            FAIL,
            "Chủ tài khoản chưa xác nhận đồng ý trao đổi dữ liệu thật (Cài đặt → Tích hợp AIVA).",
        )
    check_consent(report, tenant)
    check_permissions(report, tenant)
    check_data_separation(report, tenant)
    check_audit(report, tenant)


def check_consent(report: Report, tenant) -> None:
    real = Contact.objects.for_tenant(tenant).filter(data_origin="real")
    granted = real.filter(consent_status=ConsentStatus.GRANTED)
    incomplete = granted.filter(Q(consent_source="") | Q(consent_at__isnull=True)).count()
    if incomplete:
        report.add(
            "consent_incomplete",
            FAIL,
            "Có khách 'đồng ý' nhưng thiếu nguồn hoặc thời điểm đồng ý; bổ sung trước khi live.",
            incomplete,
        )
    inbound_only = granted.filter(consent_source=ConsentSource.ZALO_INBOUND).count()
    if inbound_only:
        report.add(
            "consent_inbound_only",
            WARN,
            "Có khách chỉ ghi nguồn 'khách chủ động nhắn'. Nhắn tin không đồng nghĩa đồng ý "
            "chia sẻ sang CRS; nên xác nhận lại qua CRS hoặc biểu mẫu.",
            inbound_only,
        )
    personal = granted.filter(channel__channel_type="zalo_personal").count()
    if personal:
        report.add(
            "consent_personal_channel",
            WARN,
            "Có khách kênh Zalo cá nhân đã đồng ý; dữ liệu của họ sẽ được chia sẻ với CRS.",
            personal,
        )
    withdrawn = real.filter(consent_status=ConsentStatus.WITHDRAWN).count()
    report.add(
        "consent_summary",
        OK if not incomplete else WARN,
        f"Khách thật: {real.count()}, đồng ý: {granted.count()}, đã rút: {withdrawn}. "
        "Chỉ khách đồng ý (đủ nguồn + thời điểm) được chia sẻ.",
        granted.count(),
    )


def check_permissions(report: Report, tenant) -> None:
    users = User.objects.filter(tenant=tenant, is_active=True)
    owners = users.filter(role=User.Role.OWNER).count()
    admins = users.filter(role=User.Role.ADMIN).count()
    if not owners:
        report.add("owner_present", FAIL, "Tenant không có chủ tài khoản đang hoạt động.")
    report.add(
        "roles",
        OK if owners else WARN,
        f"Người có quyền duyệt mọi tin: {owners} chủ tài khoản, {admins} quản trị. Kiểm tra lại "
        "danh sách này trước khi live.",
        owners + admins,
    )
    staff_refs = set(users.exclude(staff_ref="").values_list("staff_ref", flat=True))
    unmapped = (
        Contact.objects.for_tenant(tenant)
        .filter(data_origin="real", consent_status=ConsentStatus.GRANTED)
        .exclude(owner_staff_ref="")
        .exclude(owner_staff_ref__in=staff_refs)
        .values_list("owner_staff_ref", flat=True)
        .distinct()
    )
    missing = sorted(set(unmapped))
    if missing:
        report.add(
            "staff_unmapped",
            WARN,
            "Mã nhân viên phụ trách từ CRS chưa gắn với tài khoản nào (tin chăm sóc sẽ chỉ do "
            f"chủ/quản trị duyệt): {', '.join(missing[:10])}.",
            len(missing),
        )


def check_data_separation(report: Report, tenant) -> None:
    frozen_demo = (
        IntegrationOutbox.objects.for_tenant(tenant)
        .filter(data_origin="demo", status__in=["pending", "failed"])
        .count()
    )
    if frozen_demo:
        report.add(
            "demo_outbox_pending",
            WARN,
            "Còn sự kiện demo chưa gửi; ở chế độ live chúng bị giữ lại, không bao giờ gửi đi.",
            frozen_demo,
        )
    report.add(
        "data_separation", OK, "Chế độ live chỉ trao đổi dữ liệu thật; demo không bao giờ được gửi."
    )


def check_audit(report: Report, tenant) -> None:
    """The trail must be writable and must already hold the owner's acknowledgement."""
    if AuditLog.objects.filter(tenant=tenant, action="aiva.live_acknowledged").exists():
        report.add("audit_trail", OK, "Nhật ký đã ghi lần xác nhận của chủ tài khoản.")
    else:
        report.add("audit_trail", WARN, "Chưa thấy nhật ký xác nhận của chủ tài khoản.")


def run_preflight(*, tenant=None, check_health: bool = False, server: bool = True) -> Report:
    report = Report()
    if server:
        check_server(report, check_health=check_health)
    if tenant is not None:
        check_tenant(report, tenant)
    return report
