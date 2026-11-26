"""Celery tasks of the AI engine."""

from __future__ import annotations

from celery import shared_task


@shared_task(ignore_result=True)
def process_inbound_message(message_id: int):
    from .pipeline import process_inbound_message as run

    result = run(message_id)
    return result.outcome if result else None


@shared_task(ignore_result=True)
def retry_auto_reply(run_id: int, attempt: int):
    from .models import AIReplyLog
    from .pipeline import send_auto_reply

    run = AIReplyLog.objects.filter(pk=run_id, outcome=AIReplyLog.Outcome.RETRY_SCHEDULED).first()
    if run is None:
        return None
    return send_auto_reply(run, attempt, delayed=True).outcome


@shared_task(ignore_result=True)
def update_conversation_memory(conversation_id: int):
    from apps.conversations.models import Conversation
    from apps.memory.services import update_memory

    conversation = (
        Conversation.objects.select_related("tenant", "contact").filter(pk=conversation_id).first()
    )
    if conversation is None:
        return False
    return update_memory(conversation)


@shared_task(ignore_result=True)
def expire_approvals():
    from apps.memory.services import purge_expired_short_term
    from apps.permissions.services import expire_overdue

    return {"approvals_expired": expire_overdue(), "short_term_purged": purge_expired_short_term()}
