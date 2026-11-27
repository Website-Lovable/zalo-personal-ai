"""Phase 6: the gate in front of live (real-data) exchange with AIVA, and its state changes.

Live exchange for a tenant needs ALL of:
1. ``AIVA_INTEGRATION_MODE=live`` and ``AIVA_LIVE_APPROVED=true`` (server config, set after
   the director's written approval, quoted in ``AIVA_LIVE_APPROVAL_REF``);
2. the platform switch not paused (``AIVALiveSwitch``, rollback level 2);
3. the tenant enrolled and ``active``: its owner acknowledged in the app, then the operator
   activated it after a passing preflight (``AIVALiveEnrollment``, rollback level 1).

Queue rules while live is on:
- ``active``  -> events are queued and delivered, inbound calls are served;
- ``paused`` or switch paused -> events are still queued but frozen (delivered on resume),
  inbound calls answer 503;
- not enrolled / ``acknowledged`` / ``revoked`` -> nothing is queued, inbound answers 503.

Every state change writes an ``AuditLog`` entry (Kickoff rule 7).
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.audit import log_action

from . import config
from .models import AIVALiveEnrollment, AIVALiveSwitch, IntegrationAuditLog, IntegrationOutbox

Status = AIVALiveEnrollment.Status

#: What the tenant owner confirms in the app before real data may flow (versioned).
ACKNOWLEDGEMENT_VERSION = "v1"
ACKNOWLEDGEMENT_TEXT = (
    "Tôi là chủ tài khoản và đồng ý cho Zalo AI trao đổi dữ liệu THẬT với hệ thống AIVA/CRS: "
    "chỉ khách hàng đã đồng ý liên hệ (có nguồn và thời điểm đồng ý), chỉ bản tóm tắt hội "
    "thoại, nhu cầu và đề xuất cập nhật hồ sơ, không gửi toàn bộ lịch sử chat. Tin chăm sóc do "
    "AIVA soạn luôn phải được nhân viên phụ trách duyệt trước khi gửi. Tôi có thể tạm dừng "
    "hoặc thu hồi xác nhận này bất cứ lúc nào."
)


class LiveStateError(Exception):
    """A state change that is not allowed from the current state."""


# --- reading the gate ------------------------------------------------------------------


def get_switch() -> AIVALiveSwitch:
    switch, _ = AIVALiveSwitch.objects.get_or_create(pk=1)
    return switch


def is_globally_paused() -> bool:
    return AIVALiveSwitch.objects.filter(pk=1, paused=True).exists()


def enrollment_for(tenant) -> AIVALiveEnrollment | None:
    if tenant is None:
        return None
    return AIVALiveEnrollment.objects.filter(tenant=tenant).first()


def can_enqueue(tenant) -> tuple[bool, str]:
    """May a live event for this tenant enter the outbox? (Non-live modes: always yes here.)"""
    if config.get_mode() != config.LIVE:
        return True, ""
    if not config.live_approved():
        return False, "live mode not approved"
    enrollment = enrollment_for(tenant)
    if enrollment is None or enrollment.status not in (Status.ACTIVE, Status.PAUSED):
        return False, "tenant not activated for live"
    return True, ""


def can_exchange(tenant) -> tuple[bool, str]:
    """May data for this tenant actually cross to/from AIVA right now?"""
    if config.get_mode() != config.LIVE:
        return True, ""
    if not config.live_approved():
        return False, "live mode not approved"
    if is_globally_paused():
        return False, "live exchange paused platform-wide"
    enrollment = enrollment_for(tenant)
    if enrollment is None or enrollment.status != Status.ACTIVE:
        state = enrollment.status if enrollment else "not enrolled"
        return False, f"tenant live state: {state}"
    return True, ""


def active_tenant_ids() -> list[int]:
    return list(
        AIVALiveEnrollment.objects.filter(status=Status.ACTIVE).values_list("tenant_id", flat=True)
    )


def record_mode() -> str | None:
    """Audit a change of AIVA_INTEGRATION_MODE since we last looked. Returns the old mode."""
    mode = config.get_mode()
    with transaction.atomic():
        switch, _ = AIVALiveSwitch.objects.select_for_update().get_or_create(pk=1)
        previous = switch.last_seen_mode
        if previous == mode:
            return None
        switch.last_seen_mode = mode
        switch.save(update_fields=["last_seen_mode"])
        log_action(
            "aiva.mode_changed",
            changes={
                "from": previous or None,
                "to": mode,
                "live_approved": config.live_approved(),
                "approval_ref": settings.AIVA_LIVE_APPROVAL_REF,
                "client": config.client_backend(),
            },
        )
    return previous


# --- state changes (all audited) ---------------------------------------------------------


def _audit(action: str, enrollment: AIVALiveEnrollment, *, user=None, request=None, **changes):
    log_action(
        action,
        tenant=enrollment.tenant,
        user=user,
        obj=enrollment,
        request=request,
        changes={"status": enrollment.status, **changes},
    )


def acknowledge(tenant, user, *, request=None) -> AIVALiveEnrollment:
    """The tenant owner confirms real data may be shared (step 1 of 2)."""
    if not user.has_role("owner"):
        raise LiveStateError("Chỉ chủ tài khoản được xác nhận.")
    with transaction.atomic():
        enrollment, _ = AIVALiveEnrollment.objects.select_for_update().get_or_create(
            tenant=tenant, defaults={"status": Status.ACKNOWLEDGED}
        )
        if enrollment.status in (Status.ACTIVE, Status.PAUSED):
            raise LiveStateError("Tenant đã được kích hoạt; không cần xác nhận lại.")
        enrollment.status = Status.ACKNOWLEDGED
        enrollment.acknowledged_by = user
        enrollment.acknowledged_at = timezone.now()
        enrollment.acknowledgement_version = ACKNOWLEDGEMENT_VERSION
        enrollment.acknowledgement_text = ACKNOWLEDGEMENT_TEXT
        enrollment.save()
        _audit(
            "aiva.live_acknowledged",
            enrollment,
            user=user,
            request=request,
            version=ACKNOWLEDGEMENT_VERSION,
        )
    return enrollment


def activate(tenant, *, operator: str, approval_ref: str) -> AIVALiveEnrollment:
    """The operator switches the tenant to live (step 2 of 2). Preflight must pass first."""
    from .preflight import run_preflight

    if not operator.strip() or not approval_ref.strip():
        raise LiveStateError("Cần ghi người thực hiện và số/ngày văn bản GĐ phê duyệt.")
    report = run_preflight(tenant=tenant)
    if not report.passed:
        raise LiveStateError("Preflight chưa đạt: " + ", ".join(c.code for c in report.failures))
    with transaction.atomic():
        enrollment = AIVALiveEnrollment.objects.select_for_update().filter(tenant=tenant).first()
        if enrollment is None or enrollment.status != Status.ACKNOWLEDGED:
            raise LiveStateError("Chủ tài khoản chưa xác nhận (hoặc đã thu hồi xác nhận).")
        enrollment.status = Status.ACTIVE
        enrollment.activated_by = operator
        enrollment.activated_at = timezone.now()
        enrollment.approval_ref = approval_ref
        enrollment.save()
        _audit(
            "aiva.live_activated",
            enrollment,
            operator=operator,
            approval_ref=approval_ref,
            preflight=report.summary(),
        )
    return enrollment


def pause(tenant, *, by: str, reason: str, user=None, request=None) -> AIVALiveEnrollment:
    """Rollback level 1: stop one tenant. Queued events stay frozen until resume."""
    if not reason.strip():
        raise LiveStateError("Cần ghi lý do tạm dừng.")
    with transaction.atomic():
        enrollment = AIVALiveEnrollment.objects.select_for_update().filter(tenant=tenant).first()
        if enrollment is None or enrollment.status != Status.ACTIVE:
            raise LiveStateError("Tenant không ở trạng thái đang kết nối.")
        enrollment.status = Status.PAUSED
        enrollment.paused_at = timezone.now()
        enrollment.paused_by = by
        enrollment.pause_reason = reason
        enrollment.save()
        _audit("aiva.live_paused", enrollment, user=user, request=request, by=by, reason=reason)
    return enrollment


def resume(tenant, *, operator: str) -> AIVALiveEnrollment:
    """Operator only. Re-runs the preflight, then frozen events are delivered."""
    from .preflight import run_preflight

    report = run_preflight(tenant=tenant)
    if not report.passed:
        raise LiveStateError("Preflight chưa đạt: " + ", ".join(c.code for c in report.failures))
    with transaction.atomic():
        enrollment = AIVALiveEnrollment.objects.select_for_update().filter(tenant=tenant).first()
        if enrollment is None or enrollment.status != Status.PAUSED:
            raise LiveStateError("Tenant không ở trạng thái tạm dừng.")
        enrollment.status = Status.ACTIVE
        enrollment.save()
        _audit("aiva.live_resumed", enrollment, operator=operator, preflight=report.summary())
    return enrollment


def revoke(tenant, *, by: str, reason: str, user=None, request=None) -> AIVALiveEnrollment:
    """The owner withdraws the acknowledgement: stop, and drop real events not yet sent."""
    with transaction.atomic():
        enrollment = AIVALiveEnrollment.objects.select_for_update().filter(tenant=tenant).first()
        if enrollment is None or enrollment.status == Status.REVOKED:
            raise LiveStateError("Chưa có xác nhận để thu hồi.")
        previous = enrollment.status
        enrollment.status = Status.REVOKED
        enrollment.paused_at = timezone.now()
        enrollment.paused_by = by
        enrollment.pause_reason = reason or "revoked"
        enrollment.save()
        dropped = discard_unsent(tenant, reason="live acknowledgement revoked")
        _audit(
            "aiva.live_revoked",
            enrollment,
            user=user,
            request=request,
            by=by,
            reason=reason,
            previous=previous,
            dropped_events=dropped,
        )
    return enrollment


def discard_unsent(tenant, *, reason: str) -> int:
    """Dead-letter this tenant's real events that were never delivered (never sent later)."""
    Q = IntegrationOutbox.Status
    items = list(
        IntegrationOutbox.objects.for_tenant(tenant).filter(
            data_origin="real", status__in=[Q.PENDING, Q.FAILED]
        )
    )
    for item in items:
        item.status = Q.DEAD
        item.last_error = f"discarded: {reason}"
        item.next_attempt_at = None
        item.save(update_fields=["status", "last_error", "next_attempt_at", "updated_at"])
        IntegrationAuditLog.objects.create(
            tenant=tenant,
            direction=IntegrationAuditLog.Direction.OUTBOUND,
            mode=config.get_mode(),
            event_type=item.event_type,
            event_id=item.event_id,
            data_origin=item.data_origin,
            success=False,
            detail=item.last_error,
        )
    return len(items)


def set_global_pause(paused: bool, *, by: str, reason: str) -> AIVALiveSwitch:
    """Rollback level 2: stop (or restart) live exchange for every tenant at once."""
    if not by.strip() or (paused and not reason.strip()):
        raise LiveStateError("Cần ghi người thực hiện và lý do.")
    with transaction.atomic():
        switch, _ = AIVALiveSwitch.objects.select_for_update().get_or_create(pk=1)
        before = switch.paused
        switch.paused = paused
        switch.reason = reason
        switch.changed_by = by
        switch.changed_at = timezone.now()
        switch.save()
        log_action(
            "aiva.live_global_paused" if paused else "aiva.live_global_resumed",
            changes={"from": before, "to": paused, "by": by, "reason": reason},
        )
    return switch
