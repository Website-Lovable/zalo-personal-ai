"""AI reply pipeline for one inbound message (Blueprint B4 steps [4]-[11]).

[4] permission check -> [5] memory -> [6] prompt -> [7] LLM -> [8] post-process and safety
-> post-LLM authority check -> [9]/[10] send (or approval queue / 'Chờ chủ gửi') -> [11] log
and schedule a memory update.

Runs in a Celery worker after the natural reply delay. Idempotent per inbound message
(``AIReplyLog`` is one-to-one with it). The AI only proposes: anything that is not a plain
``auto_reply`` becomes a draft for a person (CLAUDE.md rule 3).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.conversations.models import Conversation, Message
from apps.conversations.services import ReplyNotAllowed, SendFailed, dispatch_reply
from apps.memory import services as memory
from apps.permissions import checker
from apps.permissions.models import ApprovalQueue
from common.audit import log_action
from common.exceptions import FeatureDisabledError

from . import safety
from .llm import LLMError, get_llm_provider, record_usage
from .models import AIReplyLog, OwnerProfile
from .prompt import REPLY_SCHEMA, build_prompt

logger = logging.getLogger(__name__)
Outcome = AIReplyLog.Outcome


def _parse_reply(text: str) -> tuple[str, float, bool, list[str]]:
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError
    except (TypeError, ValueError):
        # Not JSON (should not happen with structured outputs): treat as plain text and let a
        # person check it.
        return text, 0.0, False, []
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    intents = [str(i)[:40] for i in (data.get("detected_intents") or [])][:10]
    return (
        str(data.get("reply") or ""),
        max(0.0, min(1.0, confidence)),
        bool(data.get("needs_human")),
        intents,
    )


def _rate_limited(conversation: Conversation, now) -> str | None:
    sent = Message.objects.for_tenant(conversation.tenant).filter(
        direction=Message.Direction.OUTBOUND, is_ai_generated=True, was_approved__isnull=True
    )
    per_channel = sent.filter(
        conversation__channel=conversation.channel, created_at__gte=now - timedelta(hours=1)
    ).count()
    if per_channel >= settings.AI_MAX_AUTO_REPLIES_PER_CHANNEL_HOUR:
        return "rate_limited:channel"
    per_conversation = sent.filter(
        conversation=conversation, created_at__gte=now - timedelta(minutes=10)
    ).count()
    if per_conversation >= settings.AI_MAX_AUTO_REPLIES_PER_CONVERSATION_10MIN:
        return "rate_limited:conversation"
    return None


def _create_approval(conversation, message, draft, confidence, decision) -> ApprovalQueue:
    kind = (
        ApprovalQueue.Kind.ESCALATION
        if decision.action == checker.ESCALATE
        else ApprovalQueue.Kind.DRAFT
    )
    approval = ApprovalQueue.objects.create(
        tenant=conversation.tenant,
        source=ApprovalQueue.Source.INBOUND_REPLY,
        kind=kind,
        message=message,
        conversation=conversation,
        contact=conversation.contact,
        proposed_response=draft,
        ai_confidence=confidence,
        reasons=decision.reasons,
        owner_staff_ref=conversation.contact.owner_staff_ref,
        expires_at=timezone.now() + timedelta(hours=settings.APPROVAL_EXPIRY_HOURS),
        data_origin=message.data_origin,
    )
    log_action(
        f"approval.created.{kind}",
        obj=approval,
        changes={"reasons": decision.reasons, "owner_staff_ref": approval.owner_staff_ref},
    )
    return approval


def _finish(run: AIReplyLog, outcome: str, decision=None, **fields) -> AIReplyLog:
    run.outcome = outcome
    if decision is not None:
        run.decision = decision.action
        run.reasons = decision.reasons
        run.matched_rule = decision.rule
    for key, value in fields.items():
        setattr(run, key, value)
    run.save()
    return run


def _claim(message: Message) -> AIReplyLog | None:
    try:
        with transaction.atomic():
            return AIReplyLog.objects.create(
                tenant=message.tenant,
                conversation=message.conversation,
                inbound_message=message,
                outcome=Outcome.SKIPPED,
                reasons=["in_progress"],
                data_origin=message.data_origin,
            )
    except IntegrityError:
        return None  # another worker already handles this message


@dataclass
class Draft:
    draft: str = ""
    confidence: float = 0.0
    model: str = ""
    intents: list[str] = field(default_factory=list)


def generate_draft(conversation: Conversation, decision: checker.Decision, now=None) -> Draft:
    """Steps [5]-[8] plus the post-LLM authority check. Tightens ``decision`` in place.

    Raises ``LLMError`` when the provider fails.
    """
    context = memory.get_context(conversation)
    prompt = build_prompt(conversation, context, now)
    if not prompt.messages:
        decision.tighten(checker.ESCALATE, "nothing_to_answer")
        return Draft()
    result = get_llm_provider("main").generate(
        prompt.messages,
        system=prompt.volatile,
        cacheable_system=prompt.stable,
        max_tokens=settings.LLM_REPLY_MAX_TOKENS,
        json_schema=REPLY_SCHEMA,
        effort=settings.LLM_EFFORT_REPLY,
    )
    record_usage(conversation.tenant, result, purpose="reply")
    if result.refused:
        decision.tighten(checker.ESCALATE, "model_refused")
        return Draft(model=result.model)
    text, confidence, needs_human, intents = _parse_reply(result.text)
    profile = OwnerProfile.objects.filter(tenant=conversation.tenant).first()
    out = safety.check_output(
        text,
        max_chars=settings.AI_MAX_REPLY_CHARS,
        extra_topics=list(profile.topics_to_avoid or []) if profile else [],
    )
    checker.check_after_llm(
        decision, flags=out.flags, confidence=confidence, needs_human=needs_human
    )
    return Draft(out.text, confidence, result.model, intents)


def preview(conversation: Conversation) -> tuple[checker.Decision, Draft]:
    """Dry run for the owner: what would the AI answer to the latest message? Sends nothing."""
    latest = (
        Message.objects.for_tenant(conversation.tenant)
        .filter(conversation=conversation, direction=Message.Direction.INBOUND)
        .order_by("-id")
        .first()
    )
    if latest is None:
        return checker.Decision(action=checker.NO_REPLY, reasons=["no_inbound_message"]), Draft()
    decision = checker.check_before_llm(latest, conversation)
    if decision.skip or decision.action == checker.NO_REPLY:
        return decision, Draft()
    return decision, generate_draft(conversation, decision)


def process_inbound_message(message_id: int) -> AIReplyLog | None:
    message = (
        Message.objects.select_related(
            "tenant", "conversation__contact__channel", "conversation__channel"
        )
        .filter(pk=message_id, direction=Message.Direction.INBOUND)
        .first()
    )
    if message is None:
        return None
    run = _claim(message)
    if run is None:
        return None
    try:
        return _run(run, message)
    except Exception as exc:  # noqa: BLE001 - never leave a message silently unanswered
        logger.exception("AI pipeline failed for message %s", message.pk)
        return _finish(run, Outcome.FAILED, reasons=["internal_error"], error=repr(exc)[:500])


def _run(run: AIReplyLog, message: Message) -> AIReplyLog:
    conversation = message.conversation
    now = timezone.now()

    newer = Message.objects.filter(
        conversation=conversation, direction=Message.Direction.INBOUND, id__gt=message.id
    ).exists()
    if newer:
        # A burst of messages: answer once, to the latest one (it sees the whole burst).
        return _finish(run, Outcome.SKIPPED, reasons=["superseded"])

    decision = checker.check_before_llm(message, conversation, now=now)
    if decision.skip:
        return _finish(run, Outcome.SKIPPED, decision)
    if decision.action == checker.NO_REPLY:
        return _finish(run, Outcome.NOT_REPLIED, decision)

    try:
        drafted = generate_draft(conversation, decision, now)
    except LLMError as exc:
        logger.warning("LLM failed for message %s: %s", message.pk, exc)
        decision.tighten(checker.ESCALATE, "llm_error")
        approval = _create_approval(conversation, message, "", 0.0, decision)
        return _finish(run, Outcome.FAILED, decision, error=str(exc)[:500], approval=approval)
    draft, confidence = drafted.draft, drafted.confidence

    if decision.action == checker.AUTO:
        limited = _rate_limited(conversation, now)
        if limited:
            decision.tighten(checker.APPROVAL, limited)

    common = {"model": drafted.model, "confidence": confidence, "draft": draft}
    if decision.action != checker.AUTO:
        approval = _create_approval(conversation, message, draft, confidence, decision)
        outcome = (
            Outcome.ESCALATED if decision.action == checker.ESCALATE else Outcome.NEED_APPROVAL
        )
        _schedule_memory_update(conversation)
        return _finish(run, outcome, decision, approval=approval, **common)

    _finish(run, Outcome.SKIPPED, decision, **common)  # persist draft before sending
    return send_auto_reply(run)


#: Send-guard refusals worth a later retry (e.g. personal channel rate limit / time window)
#: carry ``SendFailed.retry_after``. These prefixes mean "stop", never retry.
_NO_RETRY_PREFIXES = ("guard:", "restricted:")
MAX_SEND_ATTEMPTS = 3


def _personal_human_delay(text: str) -> int:
    """Human-like typing delay for personal Zalo (Phase 2b limits, Kickoff 3 #3)."""
    try:
        from apps.channels.providers.personal_unofficial.limits import human_delay_seconds
    except ImportError:  # Phase 2b not installed
        return 0
    return max(0, int(human_delay_seconds(text)))


def _schedule_send(run: AIReplyLog, attempt: int, countdown: int):
    from .tasks import retry_auto_reply

    transaction.on_commit(
        lambda: retry_auto_reply.apply_async((run.pk, attempt), countdown=countdown)
    )


def send_auto_reply(run: AIReplyLog, attempt: int = 0, *, delayed: bool = False) -> AIReplyLog:
    """Send the draft of an ``auto_reply`` run. Re-checks every switch first.

    Personal Zalo channels first wait a human-like delay for the reply text. A refusal that
    says "try again in N seconds" (``SendFailed.retry_after``) is retried up to
    ``MAX_SEND_ATTEMPTS`` times; anything else becomes a draft for a person.
    """
    conversation = Conversation.objects.select_related("tenant", "channel", "contact").get(
        pk=run.conversation_id
    )
    message = run.inbound_message
    decision = checker.Decision(action=run.decision or checker.AUTO, reasons=list(run.reasons))
    decision.rule = run.matched_rule
    if not delayed and conversation.channel.is_personal:
        wait = _personal_human_delay(run.draft)
        if wait:
            decision.reasons.append("human_delay")
            _finish(run, Outcome.RETRY_SCHEDULED, decision)
            _schedule_send(run, attempt, wait)
            return run
    # Last check right before sending: the emergency stop or a takeover may have happened
    # while the model was thinking or while a retry was waiting.
    late = checker.skip_reason(conversation)
    if late:
        decision.reasons.append(f"late:{late}")
        return _finish(run, Outcome.SKIPPED, decision)
    try:
        sent = dispatch_reply(conversation, run.draft, reply_to=message, is_ai_generated=True)
    except ReplyNotAllowed:
        decision.reasons.append("contact_not_allowed")
        return _finish(run, Outcome.NOT_REPLIED, decision)
    except (SendFailed, FeatureDisabledError) as exc:
        error = str(exc)[:500]
        retry_after = getattr(exc, "retry_after", None)
        if (
            retry_after
            and attempt + 1 < MAX_SEND_ATTEMPTS
            and not error.startswith(_NO_RETRY_PREFIXES)
        ):
            from .tasks import retry_auto_reply

            decision.reasons.append(f"retry:{attempt + 1}")
            _finish(run, Outcome.RETRY_SCHEDULED, decision, error=error)
            transaction.on_commit(
                lambda: retry_auto_reply.apply_async(
                    (run.pk, attempt + 1), countdown=int(retry_after)
                )
            )
            return run
        decision.tighten(checker.APPROVAL, "send_failed")
        approval = _create_approval(
            conversation, message, run.draft, run.confidence or 0.0, decision
        )
        approval.send_error = error
        approval.save(update_fields=["send_error", "updated_at"])
        return _finish(run, Outcome.FAILED, decision, error=error, approval=approval)
    if sent.message is not None:
        Message.objects.filter(pk=sent.message.pk).update(ai_confidence=run.confidence)
        sent.message.ai_confidence = run.confidence
        log_action("ai.auto_reply_sent", obj=sent.message, changes={"reasons": decision.reasons})
        outcome = Outcome.SENT
    else:
        outcome = Outcome.QUEUED_FOR_OWNER
    _schedule_memory_update(conversation)
    return _finish(run, outcome, decision, output_message=sent.message, error="")


def _schedule_memory_update(conversation: Conversation):
    from .tasks import update_conversation_memory

    transaction.on_commit(lambda: update_conversation_memory.delay(conversation.pk))
