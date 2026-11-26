"""Model signals -> real-time notifications (connected in apps.py)."""

from __future__ import annotations

from django.db.models.signals import post_save

from .notifications import notify


def on_message(sender, instance, created, **kwargs):
    if created and instance.direction == "inbound":
        conversation = instance.conversation
        notify(
            instance.tenant_id,
            "message.inbound",
            conversation_id=conversation.pk,
            contact_name=conversation.contact.display_name,
        )


def on_approval(sender, instance, created, **kwargs):
    notify(
        instance.tenant_id,
        "approval.created" if created else "approval.updated",
        id=instance.pk,
        kind=instance.kind,
        source=instance.source,
        status=instance.status,
        owner_staff_ref=instance.owner_staff_ref,
        contact_name=instance.contact.display_name if instance.contact_id else "",
    )


def on_owner_send(sender, instance, created, **kwargs):
    notify(
        instance.tenant_id,
        "owner_send.created" if created else "owner_send.updated",
        id=instance.pk,
        status=instance.status,
    )


def on_ai_control(sender, instance, created, **kwargs):
    notify(instance.tenant_id, "ai.control", emergency_stop=instance.emergency_stop)


def on_channel(sender, instance, created, **kwargs):
    notify(
        instance.tenant_id,
        "channel.updated",
        id=instance.pk,
        status=instance.status,
        name=instance.name,
    )


def on_outbox(sender, instance, created, **kwargs):
    if instance.status == "dead":
        notify(instance.tenant_id, "aiva.outbox_dead", id=instance.pk, event=instance.event_type)


def connect():
    from apps.ai_engine.models import AIControl
    from apps.channels.models import Channel
    from apps.conversations.models import Message, PendingOwnerSend
    from apps.integrations.aiva.models import IntegrationOutbox
    from apps.permissions.models import ApprovalQueue

    for model, handler in (
        (Message, on_message),
        (ApprovalQueue, on_approval),
        (PendingOwnerSend, on_owner_send),
        (AIControl, on_ai_control),
        (Channel, on_channel),
        (IntegrationOutbox, on_outbox),
    ):
        post_save.connect(handler, sender=model, dispatch_uid=f"dashboard.notify.{model.__name__}")
