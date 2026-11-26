"""Business logic of the AIVA -> Zalo AI endpoints (Kickoff 2.2). Views live in ``views.py``.

Every function takes the tenant resolved from the signed request and returns
``(http_status, body)``. Guards:
- data_origin must be allowed in the current mode (sandbox: demo only) and must match the
  records it touches; demo and real data never mix;
- CRS is the source of truth: an upsert overwrites the Zalo AI copy of the customer;
- an outreach draft only enters the approval queue of the staff in charge; nothing is sent
  until a person approves it, and only to consented contacts outside the personal channel.
"""

from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from jsonschema import Draft202012Validator, FormatChecker

from apps.ai_engine import safety
from apps.channels.models import Channel
from apps.contacts.models import ConsentSource, Contact, ReplyPermission
from apps.conversations.models import Conversation
from apps.memory.services import current_short_term
from apps.permissions.models import ApprovalQueue
from common.audit import log_action

from . import config
from .config import SCHEMA_VERSION

INBOUND_SCHEMA_PATH = settings.BASE_DIR / "docs" / "integration" / "aiva-v1-inbound.schema.json"

Result = tuple[int, dict[str, Any]]


def error(status: int, code: str, detail: Any) -> Result:
    return status, {"code": code, "detail": detail}


@lru_cache(maxsize=1)
def load_inbound_schema() -> dict[str, Any]:
    return json.loads(INBOUND_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    schema = load_inbound_schema()
    return Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}, format_checker=FormatChecker()
    )


def validate(name: str, data: Any) -> list[str]:
    errors = sorted(_validator(name).iter_errors(data), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def _origin_allowed(origin: str) -> bool:
    return origin in config.allowed_data_origins()


def _origin_error(origin: str) -> Result:
    return error(
        422,
        "data_origin_not_allowed",
        f"data_origin={origin} không được phép ở chế độ {config.get_mode()}.",
    )


# --- POST contacts/upsert ------------------------------------------------------------


def upsert_contact(tenant, data: dict[str, Any]) -> Result:
    problems = validate("contacts_upsert", data)
    if problems:
        return error(400, "invalid_body", problems)
    origin = data["data_origin"]
    if not _origin_allowed(origin):
        return _origin_error(origin)

    contacts = Contact.objects.for_tenant(tenant).select_related("channel")
    contact = contacts.filter(crs_customer_id=data["crs_customer_id"]).first()
    created = False
    if contact is None and data.get("zalo_user_id"):
        channel = (
            Channel.objects.for_tenant(tenant)
            .filter(pk=int(data["channel_ref"]) if data["channel_ref"].isdigit() else None)
            .first()
        )
        if channel is None:
            return error(422, "unknown_channel", "channel_ref không thuộc tenant này.")
        if channel.data_origin != origin:
            return error(409, "data_origin_mismatch", "Kênh khác loại dữ liệu (demo/real).")
        contact = contacts.filter(channel=channel, zalo_user_id=data["zalo_user_id"]).first()
        if contact is None:
            contact = Contact(
                tenant=tenant,
                channel=channel,
                zalo_user_id=data["zalo_user_id"],
                data_origin=origin,
            )
            created = True
        elif contact.crs_customer_id and contact.crs_customer_id != data["crs_customer_id"]:
            return error(409, "crs_id_conflict", "Liên hệ đã gắn với mã khách CRS khác.")
    if contact is None:
        return error(
            404,
            "contact_unknown",
            "Chưa có liên hệ với crs_customer_id này; gửi kèm channel_ref + zalo_user_id.",
        )
    if contact.data_origin != origin:
        return error(409, "data_origin_mismatch", "Liên hệ khác loại dữ liệu (demo/real).")

    before = {
        "consent_status": contact.consent_status,
        "owner_staff_ref": contact.owner_staff_ref,
        "segment": contact.segment,
    }
    contact.crs_customer_id = data["crs_customer_id"]
    contact.display_name = data["display_name"]
    if "segment" in data:
        contact.segment = data["segment"]
    if "owner_staff_ref" in data:
        contact.owner_staff_ref = data["owner_staff_ref"]
    contact.set_consent(
        data["consent_status"], ConsentSource.CRS, at=parse_datetime(data["consent_at"])
    )
    contact.save()
    log_action(
        "contact.crs_upserted",
        tenant=tenant,
        obj=contact,
        changes={
            "created": created,
            "before": before,
            "consent_status": contact.consent_status,
            "consent_source": contact.consent_source,
            "consent_at": contact.consent_at.isoformat() if contact.consent_at else None,
            "owner_staff_ref": contact.owner_staff_ref,
        },
    )
    return (201 if created else 200), {
        "contact_ref": str(contact.pk),
        "created": created,
        "consent_status": contact.consent_status,
        "crs_customer_id": contact.crs_customer_id,
    }


# --- POST outreach/draft -------------------------------------------------------------


def outreach_draft(tenant, data: dict[str, Any]) -> Result:
    problems = validate("outreach_draft", data)
    if problems:
        return error(400, "invalid_body", problems)
    origin = data["data_origin"]
    if not _origin_allowed(origin):
        return _origin_error(origin)

    contacts = Contact.objects.for_tenant(tenant).select_related("channel")
    if data.get("contact_ref"):
        ref = data["contact_ref"]
        contact = contacts.filter(pk=int(ref)).first() if ref.isdigit() else None
    else:
        contact = contacts.filter(crs_customer_id=data["crs_customer_id"]).first()
    if contact is None:
        return error(404, "contact_unknown", "Không tìm thấy liên hệ.")
    if contact.data_origin != origin:
        return error(409, "data_origin_mismatch", "Liên hệ khác loại dữ liệu (demo/real).")
    if not contact.has_consent:
        return error(422, "consent_required", "Khách chưa đồng ý nhận tin chăm sóc.")
    if contact.channel.is_personal:
        return error(
            422,
            "personal_channel_outreach_forbidden",
            "Kênh Zalo cá nhân chỉ trả lời người nhắn trước, không gửi tin chủ động.",
        )
    if contact.reply_permission == ReplyPermission.DO_NOT_REPLY:
        return error(422, "contact_do_not_contact", "Liên hệ được đặt là không liên hệ.")
    if not contact.channel.is_active:
        return error(422, "channel_inactive", "Kênh của liên hệ đang tắt.")

    staff = data.get("owner_staff_ref") or contact.owner_staff_ref
    hours = data.get("expires_in_hours") or settings.APPROVAL_EXPIRY_HOURS
    conversation = (
        Conversation.objects.for_tenant(tenant)
        .filter(contact=contact, channel=contact.channel)
        .exclude(status=Conversation.Status.ARCHIVED)
        .first()
    )
    reasons = ["aiva_outreach"] + [
        f"sensitive:{code}" for code in safety.sensitive_topics(data["draft_text"])
    ]
    approval = ApprovalQueue.objects.create(
        tenant=tenant,
        source=ApprovalQueue.Source.AIVA_OUTREACH,
        kind=ApprovalQueue.Kind.DRAFT,
        contact=contact,
        conversation=conversation,
        proposed_response=data["draft_text"],
        ai_confidence=0.0,
        reasons=reasons,
        owner_staff_ref=staff,
        expires_at=timezone.now() + timedelta(hours=hours),
        data_origin=origin,
    )
    log_action(
        "approval.created.aiva_outreach",
        tenant=tenant,
        obj=approval,
        changes={
            "owner_staff_ref": staff,
            "purpose": data.get("purpose", ""),
            "request_ref": data.get("request_ref", ""),
            "reasons": reasons,
        },
    )
    return 201, {
        "approval_ref": str(approval.pk),
        "status": "pending",
        "owner_staff_ref": staff,
        "expires_at": approval.expires_at.isoformat(),
    }


# --- GET conversations/{id}/summary --------------------------------------------------


def conversation_summary(tenant, conversation_id: int) -> Result:
    from .events import recent_intents

    conversation = (
        Conversation.objects.for_tenant(tenant)
        .select_related("contact")
        .filter(pk=conversation_id)
        .first()
    )
    if conversation is None or not _origin_allowed(conversation.data_origin):
        return error(404, "conversation_unknown", "Không tìm thấy hội thoại.")
    contact = conversation.contact
    if not contact.has_consent:
        return error(403, "consent_required", "Khách chưa đồng ý chia sẻ thông tin.")
    stm = current_short_term(conversation)
    body = {
        "conversation_ref": str(conversation.pk),
        "contact_ref": str(contact.pk),
        "summary": stm.context_summary if stm else "",
        "key_points": list(stm.key_points or []) if stm else [],
        "detected_needs": recent_intents(conversation),
        "last_message_at": (
            conversation.last_message_at.isoformat() if conversation.last_message_at else None
        ),
        "summary_updated_at": stm.created_at.isoformat() if stm else None,
        "data_origin": conversation.data_origin,
    }
    if contact.crs_customer_id:
        body["crs_customer_id"] = contact.crs_customer_id
    return 200, body


def health(tenant) -> Result:
    return 200, {
        "status": "ok",
        "mode": config.get_mode(),
        "schema_version": SCHEMA_VERSION,
        "tenant_ref": tenant.external_ref,
        "server_time": timezone.now().isoformat(),
    }
