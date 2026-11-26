"""Outbound dispatch: auto channels send through the provider, assist channels queue a draft.

Phase 1 scope: the routing rule and the 'Chờ chủ gửi' inbox. Permission checks, approval
queue, rate limits and emergency stop are wired in Phase 3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.channels.models import ChannelMode
from apps.channels.providers import get_provider
from common.audit import log_action

from .models import Conversation, Message, PendingOwnerSend


class ReplyNotAllowed(Exception):
    pass


class SendFailed(RuntimeError):
    """The provider refused the message (error kept for the caller / audit)."""

    def __init__(self, error: str, code: int | None = None):
        super().__init__(error)
        self.code = code


@dataclass
class DispatchResult:
    sent: bool
    message: Message | None = None
    pending: PendingOwnerSend | None = None


@transaction.atomic
def dispatch_reply(
    conversation: Conversation,
    text: str,
    *,
    reply_to: Message | None = None,
    is_ai_generated: bool = True,
) -> DispatchResult:
    contact = conversation.contact
    channel = conversation.channel
    if not contact.can_receive_reply():
        raise ReplyNotAllowed(f"Contact {contact.pk} may not receive a reply")

    if channel.mode == ChannelMode.ASSIST:
        pending = PendingOwnerSend.objects.create(
            tenant=conversation.tenant,
            channel=channel,
            conversation=conversation,
            reply_to=reply_to,
            draft_text=text,
            data_origin=conversation.data_origin,
        )
        log_action("reply.queued_for_owner", obj=pending, tenant=conversation.tenant)
        return DispatchResult(sent=False, pending=pending)

    result = get_provider(channel).send_text(contact.zalo_user_id, text)
    if not result.ok:
        raise SendFailed(result.error, result.error_code)
    now = timezone.now()
    message = Message.objects.create(
        tenant=conversation.tenant,
        conversation=conversation,
        direction=Message.Direction.OUTBOUND,
        content=text,
        is_ai_generated=is_ai_generated,
        zalo_message_id=result.message_id,
        zalo_timestamp=now,
        idempotency_key=f"out-{uuid.uuid4()}",
        data_origin=conversation.data_origin,
    )
    conversation.last_message_at = now
    conversation.save(update_fields=["last_message_at", "updated_at"])
    return DispatchResult(sent=True, message=message)
