"""Authority / permission checker (Blueprint B3 #6, B4 step [4]).

``check_before_llm`` decides whether the AI may act on an inbound message at all and, if so,
the strictest action allowed: auto_reply < need_approval < escalate < do_not_reply.
``check_after_llm`` can only make that decision stricter (Kickoff 4: the LLM never decides
to send when a rule says ``need_approval``).

Order of precedence (first match wins for "skip"; for actions the strictest wins):
1. Platform switch, tenant emergency stop, channel paused, conversation AI off / taken over.
2. Contact may not receive a reply (do_not_reply, consent withdrawn, personal channel and the
   contact has not written first).
3. Active PermissionRules by priority (first rule whose conditions all match).
4. Otherwise the strictest default of the contact's groups, else the contact's own setting.
5. Floors that rules cannot lower: sensitive topics (price, contract, money, owner's
   topics_to_avoid) -> need_approval; contact asks for a person or sends non-text -> escalate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from django.conf import settings
from django.utils import timezone

from apps.ai_engine import safety
from apps.channels.models import Channel

from .models import PermissionRule, RuleAction

AUTO = RuleAction.AUTO_REPLY.value
APPROVAL = RuleAction.NEED_APPROVAL.value
ESCALATE = RuleAction.ESCALATE.value
NO_REPLY = RuleAction.DO_NOT_REPLY.value

_STRICTNESS = {AUTO: 0, APPROVAL: 1, ESCALATE: 2, NO_REPLY: 3}


def stricter(a: str, b: str) -> str:
    return a if _STRICTNESS[a] >= _STRICTNESS[b] else b


@dataclass
class Decision:
    action: str = AUTO
    skip: bool = False  # True = AI does nothing (not even a draft)
    reasons: list[str] = field(default_factory=list)
    rule: PermissionRule | None = None

    def tighten(self, action: str, reason: str):
        if _STRICTNESS[action] > _STRICTNESS[self.action]:
            self.action = action
        if reason not in self.reasons:
            self.reasons.append(reason)

    @property
    def needs_draft(self) -> bool:
        return not self.skip and self.action in (AUTO, APPROVAL, ESCALATE)


def _in_time_range(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # overnight window, e.g. 22:00-07:00


def rule_matches(rule: PermissionRule, contact, text: str, now: time, group_ids) -> bool:
    if rule.contact_group_id and rule.contact_group_id not in group_ids:
        return False
    if rule.relationship_type and rule.relationship_type != contact.relationship_type:
        return False
    if rule.time_range_start and rule.time_range_end:
        if not _in_time_range(now, rule.time_range_start, rule.time_range_end):
            return False
    if rule.keyword_triggers:
        if not any(safety._has(text, str(k).strip()) for k in rule.keyword_triggers if k):
            return False
    return True


def skip_reason(conversation, *, now=None) -> str | None:
    """Why the AI must stay silent in this conversation right now (None = may act)."""
    from apps.ai_engine.models import AIControl

    if not settings.AI_ENGINE_ENABLED:
        return "ai_engine_disabled"
    if AIControl.for_tenant(conversation.tenant).emergency_stop:
        return "emergency_stop"
    if not conversation.tenant.is_active:
        return "tenant_inactive"
    channel = conversation.channel
    if not channel.is_active or channel.status != Channel.Status.ACTIVE:
        return "channel_not_active"
    if not conversation.ai_enabled:
        return "conversation_ai_off"
    if conversation.status != conversation.Status.ACTIVE:
        return "conversation_not_active"
    if conversation.takeover_active(now):
        return "human_takeover"
    return None


def check_before_llm(message, conversation, *, now=None) -> Decision:
    now = now or timezone.now()
    reason = skip_reason(conversation, now=now)
    if reason:
        return Decision(action=NO_REPLY, skip=True, reasons=[reason])

    contact = conversation.contact
    if not contact.can_receive_reply():
        return Decision(action=NO_REPLY, reasons=["contact_not_allowed"])

    text = message.content or ""
    group_ids = set(contact.groups.values_list("id", flat=True))
    local_now = timezone.localtime(now).time()

    decision = Decision()
    rules = PermissionRule.objects.for_tenant(conversation.tenant).filter(is_active=True)
    for rule in rules.order_by("-priority", "id"):
        if rule_matches(rule, contact, text, local_now, group_ids):
            decision.action = rule.action
            decision.rule = rule
            decision.reasons.append(f"rule:{rule.pk}")
            break
    else:
        group_defaults = list(contact.groups.values_list("default_permission", flat=True))
        if group_defaults:
            action = group_defaults[0]
            for other in group_defaults[1:]:
                action = stricter(action, other)
            decision.action = action
            decision.reasons.append("group_default")
        else:
            decision.action = contact.reply_permission
            decision.reasons.append("contact_default")

    if decision.action == NO_REPLY:
        return decision

    if message.message_type != message.MessageType.TEXT or not text.strip():
        decision.tighten(ESCALATE, f"non_text:{message.message_type}")
    if safety.asks_for_human(text):
        decision.tighten(ESCALATE, "handoff_requested")
    profile_topics = _topics_to_avoid(conversation.tenant)
    for topic in safety.sensitive_topics(text, profile_topics):
        decision.tighten(APPROVAL, f"sensitive:{topic}")
    return decision


def check_after_llm(decision: Decision, *, flags: list[str], confidence: float, needs_human):
    """Post-LLM authority check. Only ever makes the decision stricter."""
    for flag in flags:
        decision.tighten(APPROVAL, f"output:{flag}")
    if needs_human:
        decision.tighten(ESCALATE, "model_needs_human")
    if confidence < settings.AI_MIN_AUTO_CONFIDENCE:
        decision.tighten(APPROVAL, "low_confidence")
    return decision


def _topics_to_avoid(tenant) -> list[str]:
    from apps.ai_engine.models import OwnerProfile

    profile = OwnerProfile.objects.filter(tenant=tenant).first()
    return list(profile.topics_to_avoid or []) if profile else []
