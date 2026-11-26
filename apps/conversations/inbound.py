"""Inbound pipeline steps [2]-[3] of Blueprint B4: dedupe, identify contact, store message.

Steps [4]-[11] (permission check, memory, LLM, approval, send) live in
``apps.ai_engine.pipeline`` and hook in through the ``inbound_message_received`` signal.
"""

from __future__ import annotations

import logging

import django.dispatch
from django.utils import timezone

from apps.channels.providers.base import ChannelEvent
from apps.contacts.models import Contact

from .models import Conversation, Message

logger = logging.getLogger(__name__)

#: Sent after a new inbound message is stored. kwargs: message, conversation, channel.
inbound_message_received = django.dispatch.Signal()

_VALID_TYPES = {choice for choice, _ in Message.MessageType.choices}


def handle_channel_event(channel, event: ChannelEvent) -> bool:
    """Apply one normalized event. Returns False when there was nothing to do."""
    if event.kind == "ignore" or not event.user_id:
        return False
    if event.kind in ("follow", "unfollow"):
        contact, _ = _get_or_create_contact(channel, event.user_id, "")
        contact.is_follower = event.kind == "follow"
        contact.save(update_fields=["is_follower", "updated_at"])
        return True
    if event.kind == "message" and event.message is not None:
        return _store_message(channel, event)
    return False


def _get_or_create_contact(channel, user_id: str, name: str) -> tuple[Contact, bool]:
    contact, created = Contact.objects.get_or_create(
        tenant=channel.tenant,
        channel=channel,
        zalo_user_id=user_id,
        defaults={
            "display_name": name or f"Zalo {user_id[-6:]}",
            "data_origin": channel.data_origin,
        },
    )
    if created and not name:
        _enrich_from_zalo(channel, contact)
    elif name and contact.display_name != name and contact.display_name.startswith("Zalo "):
        contact.display_name = name[:255]
        contact.save(update_fields=["display_name", "updated_at"])
    return contact, created


def _enrich_from_zalo(channel, contact: Contact):
    """Best effort: fetch name/avatar for a new OA contact. Never fails the pipeline."""
    from django.conf import settings

    from apps.channels.providers import provider_class_for
    from apps.channels.providers.http import ZaloAPIError
    from apps.channels.services import upsert_contact_from_zalo

    if settings.ZALO_CHANNEL_BACKEND != "real" or not channel.access_token:
        return
    provider = provider_class_for(channel.channel_type)(channel)
    if not hasattr(provider, "get_user_detail"):
        return
    try:
        detail = provider.get_user_detail(contact.zalo_user_id)
    except ZaloAPIError as exc:
        logger.info("User detail unavailable for contact %s: %s", contact.pk, exc)
        return
    upsert_contact_from_zalo(channel, contact.zalo_user_id, detail)
    contact.refresh_from_db()


def _conversation_for(channel, contact) -> Conversation:
    conversation = (
        Conversation.objects.filter(tenant=channel.tenant, channel=channel, contact=contact)
        .exclude(status=Conversation.Status.ARCHIVED)
        .first()
    )
    if conversation is None:
        conversation = Conversation.objects.create(
            tenant=channel.tenant,
            channel=channel,
            contact=contact,
            data_origin=channel.data_origin,
        )
    return conversation


def _store_message(channel, event: ChannelEvent) -> bool:
    msg = event.message
    contact, _ = _get_or_create_contact(channel, event.user_id, msg.sender_name)
    if not event.outbound and not contact.has_messaged_first:
        contact.has_messaged_first = True
        contact.save(update_fields=["has_messaged_first", "updated_at"])
    conversation = _conversation_for(channel, contact)
    if Message.objects.filter(conversation=conversation, zalo_message_id=msg.message_id).exists():
        return False  # e.g. echo of a reply we sent through the API ourselves
    direction = Message.Direction.OUTBOUND if event.outbound else Message.Direction.INBOUND
    sent_at = msg.sent_at or timezone.now()
    message, created = Message.objects.get_or_create(
        idempotency_key=f"zalo:{channel.pk}:{direction}:{msg.message_id}"[:255],
        defaults={
            "tenant": channel.tenant,
            "conversation": conversation,
            "direction": direction,
            "content": msg.text,
            "message_type": msg.message_type if msg.message_type in _VALID_TYPES else "other",
            "zalo_message_id": msg.message_id[:100],
            "zalo_timestamp": sent_at,
            "data_origin": channel.data_origin,
        },
    )
    if not created:
        return False
    if not conversation.last_message_at or sent_at > conversation.last_message_at:
        conversation.last_message_at = sent_at
        conversation.save(update_fields=["last_message_at", "updated_at"])
    if event.outbound:
        # The owner replied directly in Zalo (not through this app): stay quiet for a while.
        from apps.ai_engine.control import auto_take_over

        auto_take_over(conversation)
    else:
        inbound_message_received.send(
            sender=Message, message=message, conversation=conversation, channel=channel
        )
    return True
