"""Signal receivers that turn app events into AIVA outbox rows (connected in apps.py).

Using signals keeps every AIVA touchpoint inside this module (CLAUDE.md rule 9): the AI
engine, memory and approval code do not import the integration.
"""

from __future__ import annotations

from django.db.models.signals import post_save

from . import events


def on_inbound_message(sender, message, conversation, channel, **kwargs):
    events.lead_inbound(conversation.contact)


def on_short_term_memory(sender, instance, created, **kwargs):
    if created:
        events.conversation_summary(instance)


def on_long_term_memory(sender, instance, created, **kwargs):
    if created:
        events.profile_update_suggested(instance)


def on_ai_run(sender, instance, created, **kwargs):
    if instance.intents:
        events.opportunity_suggested(instance)


def on_approval(sender, instance, created, **kwargs):
    if created and instance.kind == "escalation":
        events.handoff_requested(instance)


def connect():
    from apps.ai_engine.models import AIReplyLog
    from apps.conversations.inbound import inbound_message_received
    from apps.memory.models import LongTermMemory, ShortTermMemory
    from apps.permissions.models import ApprovalQueue

    inbound_message_received.connect(on_inbound_message, dispatch_uid="aiva.lead_inbound")
    post_save.connect(on_short_term_memory, sender=ShortTermMemory, dispatch_uid="aiva.summary")
    post_save.connect(on_long_term_memory, sender=LongTermMemory, dispatch_uid="aiva.profile")
    post_save.connect(on_ai_run, sender=AIReplyLog, dispatch_uid="aiva.opportunity")
    post_save.connect(on_approval, sender=ApprovalQueue, dispatch_uid="aiva.handoff")
