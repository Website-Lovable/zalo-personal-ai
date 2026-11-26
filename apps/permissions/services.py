"""Approval workflow: who may decide, and sending an approved draft."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.accounts.rbac import role_at_least
from apps.conversations.models import Message
from apps.conversations.services import ReplyNotAllowed, SendFailed, dispatch_reply
from common.audit import log_action
from common.exceptions import FeatureDisabledError

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


def _back_to_pending(item: ApprovalQueue, error: str = ""):
    ApprovalQueue.objects.filter(pk=item.pk).update(
        status=ApprovalQueue.Status.PENDING,
        modified_response="",
        reviewed_by=None,
        reviewed_at=None,
        send_error=error,
        updated_at=timezone.now(),
    )
    item.refresh_from_db()


def _outreach_conversation(item: ApprovalQueue):
    from apps.conversations.models import Conversation

    if item.conversation_id:
        return item.conversation
    contact = item.contact
    conversation = (
        Conversation.objects.filter(tenant=item.tenant, channel=contact.channel, contact=contact)
        .exclude(status=Conversation.Status.ARCHIVED)
        .first()
    )
    if conversation is None:
        conversation = Conversation.objects.create(
            tenant=item.tenant,
            channel=contact.channel,
            contact=contact,
            data_origin=contact.data_origin,
        )
    return conversation


def approve(
    item: ApprovalQueue, user, *, modified: str = "", reason: str = "", request=None
) -> ApprovalQueue:
    """Approve (optionally edited) and send through the channel.

    The decision is committed first (so two reviewers cannot send twice), then the message
    is sent outside any transaction: the channel's own state (e.g. a personal channel's
    auto-stop after a refused send) must survive a failed send. If the send fails, the item
    goes back to pending with ``send_error`` so it can be retried.
    Inbound-reply drafts go out at once (assist channels: to 'Chờ chủ gửi'). Outreach drafts
    from AIVA Sales AI go to the contact's channel too, but only while the contact still
    consents and is not on a personal channel (re-checked here, at send time).
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

    conversation = item.conversation
    if item.source == ApprovalQueue.Source.AIVA_OUTREACH:
        if item.contact is None or not item.contact.can_contact_proactively():
            _back_to_pending(item)
            raise ApprovalError("Khách chưa đồng ý nhận tin chủ động hoặc kênh không cho phép.")
        conversation = _outreach_conversation(item)
    if conversation is not None:
        try:
            result = dispatch_reply(conversation, text, reply_to=item.message, is_ai_generated=True)
        except ReplyNotAllowed as exc:
            _back_to_pending(item)
            raise ApprovalError("Liên hệ này không được phép nhận tin.") from exc
        except (SendFailed, FeatureDisabledError) as exc:
            error = str(exc)[:500]
            _back_to_pending(item, error)
            log_action(
                "approval.send_failed",
                obj=item,
                user=user,
                request=request,
                changes={"error": error, "retry_after": getattr(exc, "retry_after", None)},
            )
            raise ApprovalError(f"Zalo không nhận tin: {error}", status=502) from exc
        if result.message is not None:
            Message.objects.filter(pk=result.message.pk).update(
                was_approved=True, approved_by=user, ai_confidence=item.ai_confidence
            )
            item.sent_message = result.message
        item.conversation = conversation
        item.save(update_fields=["sent_message", "conversation", "updated_at"])
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
