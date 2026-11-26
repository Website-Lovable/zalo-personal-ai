"""Approval workflow: who may decide, and sending an approved draft."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.accounts.rbac import role_at_least
from apps.conversations.models import Message
from apps.conversations.services import ReplyNotAllowed, SendFailed, dispatch_reply
from common.audit import log_action

from .models import ApprovalQueue


class ApprovalError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def can_decide(user, item: ApprovalQueue) -> bool:
    """Owner/admin decide anything. A staff member decides items assigned to them
    (``owner_staff_ref`` = their CRS staff id, Kickoff 2.0 #2)."""
    if role_at_least(user.role, "admin"):
        return True
    return bool(user.staff_ref) and user.staff_ref == item.owner_staff_ref


def expire_overdue() -> int:
    return ApprovalQueue.objects.filter(
        status=ApprovalQueue.Status.PENDING, expires_at__lte=timezone.now()
    ).update(status=ApprovalQueue.Status.EXPIRED, updated_at=timezone.now())


def _expire_if_overdue(item: ApprovalQueue):
    """Run outside the decision transaction so the expiry is kept."""
    expired = ApprovalQueue.objects.filter(
        pk=item.pk, status=ApprovalQueue.Status.PENDING, expires_at__lte=timezone.now()
    ).update(status=ApprovalQueue.Status.EXPIRED, updated_at=timezone.now())
    if expired:
        raise ApprovalError("Mục này đã hết hạn.")


def _lock_pending(item: ApprovalQueue) -> ApprovalQueue:
    locked = ApprovalQueue.objects.select_for_update().get(pk=item.pk)
    if locked.status != ApprovalQueue.Status.PENDING:
        raise ApprovalError("Mục này đã được xử lý.")
    return locked


def reject(item: ApprovalQueue, user, *, reason: str = "", request=None) -> ApprovalQueue:
    _expire_if_overdue(item)
    with transaction.atomic():
        item = _lock_pending(item)
        item.status = ApprovalQueue.Status.REJECTED
        item.reviewed_by = user
        item.reviewed_at = timezone.now()
        item.save()
    log_action(
        "approval.rejected",
        obj=item,
        user=user,
        request=request,
        changes={"owner_staff_ref": item.owner_staff_ref, "reason": reason},
    )
    return item


def approve(
    item: ApprovalQueue, user, *, modified: str = "", reason: str = "", request=None
) -> ApprovalQueue:
    """Approve (optionally edited) and send through the channel.

    Inbound-reply drafts are sent at once (assist channels: to 'Chờ chủ gửi'). If Zalo
    refuses, the item goes back to pending with ``send_error`` so it can be retried.
    Outreach drafts from AIVA only record the decision (sending is Phase 3.5).
    """
    _expire_if_overdue(item)
    with transaction.atomic():
        item = _lock_pending(item)
        text = (modified or item.proposed_response).strip()
        if not text:
            raise ApprovalError("Cần nhập nội dung trả lời trước khi duyệt.")
        item.status = ApprovalQueue.Status.MODIFIED if modified else ApprovalQueue.Status.APPROVED
        item.modified_response = modified
        item.reviewed_by = user
        item.reviewed_at = timezone.now()
        item.send_error = ""
        item.save()

        if item.source == ApprovalQueue.Source.INBOUND_REPLY and item.conversation_id:
            try:
                result = dispatch_reply(
                    item.conversation, text, reply_to=item.message, is_ai_generated=True
                )
            except ReplyNotAllowed as exc:
                raise ApprovalError("Liên hệ này không được phép nhận tin.") from exc
            except SendFailed as exc:
                transaction.set_rollback(True)
                error = str(exc)[:500]
                failed = True
            else:
                failed = False
                if result.message is not None:
                    Message.objects.filter(pk=result.message.pk).update(
                        was_approved=True, approved_by=user, ai_confidence=item.ai_confidence
                    )
                    item.sent_message = result.message
                    item.save(update_fields=["sent_message", "updated_at"])
        else:
            failed = False

    if failed:
        ApprovalQueue.objects.filter(pk=item.pk).update(send_error=error)
        item.refresh_from_db()
        log_action(
            "approval.send_failed", obj=item, user=user, request=request, changes={"error": error}
        )
        raise ApprovalError(f"Zalo không nhận tin: {error}", status=502)
    log_action(
        f"approval.{item.status}",
        obj=item,
        user=user,
        request=request,
        changes={
            "owner_staff_ref": item.owner_staff_ref,
            "reason": reason,
            "sent_message": item.sent_message_id,
        },
    )
    return item
