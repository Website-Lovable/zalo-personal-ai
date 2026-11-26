"""Dashboard numbers (Blueprint D4: dashboard API + analytics). Everything is tenant-scoped.

Days are counted in the project time zone (TIME_ZONE), so "today" matches what the owner sees.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.ai_engine.models import AIControl, AIReplyLog, LLMUsage
from apps.channels.models import Channel
from apps.contacts.models import Contact
from apps.conversations.models import Conversation, Message, PendingOwnerSend
from apps.permissions.models import ApprovalQueue
from common.models import AuditLog

MAX_DAYS = 90


def _start_of_day(day: date):
    return timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))


def overview(tenant, user=None) -> dict:
    """Headline numbers. Polled by every open dashboard, so one aggregate query per table."""
    today = _start_of_day(timezone.localdate())
    staff_ref = getattr(user, "staff_ref", "")
    data = Channel.objects.for_tenant(tenant).aggregate(
        channels=Count("id"),
        channels_active=Count("id", filter=Q(is_active=True, status="active")),
        channels_restricted=Count("id", filter=Q(status="restricted")),
    )
    data |= Contact.objects.for_tenant(tenant).aggregate(
        contacts=Count("id"),
        contacts_with_consent=Count("id", filter=Q(consent_status="granted")),
    )
    data |= Conversation.objects.for_tenant(tenant).aggregate(
        conversations=Count("id"),
        conversations_taken_over=Count("id", filter=Q(human_takeover=True)),
    )
    data |= (
        ApprovalQueue.objects.for_tenant(tenant)
        .filter(status="pending")
        .aggregate(
            pending_approvals=Count("id"),
            pending_escalations=Count("id", filter=Q(kind="escalation")),
            my_pending_approvals=Count(
                "id", filter=Q(owner_staff_ref=staff_ref) & ~Q(owner_staff_ref="")
            ),
        )
    )
    data["pending_owner_sends"] = (
        PendingOwnerSend.objects.for_tenant(tenant).filter(status="pending").count()
    )
    data |= (
        Message.objects.for_tenant(tenant)
        .filter(created_at__gte=today)
        .aggregate(
            messages_in_today=Count("id", filter=Q(direction="inbound")),
            messages_out_today=Count("id", filter=Q(direction="outbound")),
        )
    )
    data["ai_auto_sent_today"] = (
        AIReplyLog.objects.for_tenant(tenant).filter(outcome="sent", created_at__gte=today).count()
    )
    data["emergency_stop"] = AIControl.for_tenant(tenant).emergency_stop
    return data


def _series(qs, days: list[date], field="created_at", **counts) -> dict[str, list[int]]:
    rows = (
        qs.annotate(day=TruncDate(field))
        .values("day")
        .annotate(**{k: Count("id", filter=v) for k, v in counts.items()})
    )
    by_day = {row["day"]: row for row in rows}
    return {k: [by_day.get(d, {}).get(k, 0) for d in days] for k in counts}


def analytics(tenant, days: int = 7) -> dict:
    days = max(1, min(int(days), MAX_DAYS))
    end = timezone.localdate()
    day_list = [end - timedelta(days=days - 1 - i) for i in range(days)]
    since = _start_of_day(day_list[0])

    messages = Message.objects.for_tenant(tenant).filter(created_at__gte=since)
    message_series = _series(
        messages,
        day_list,
        inbound=Q(direction="inbound"),
        outbound=Q(direction="outbound"),
        ai_sent=Q(direction="outbound", is_ai_generated=True, was_approved__isnull=True),
        approved_sent=Q(direction="outbound", was_approved=True),
    )

    runs = AIReplyLog.objects.for_tenant(tenant).filter(created_at__gte=since)
    outcomes = dict(runs.values_list("outcome").annotate(n=Count("id")).values_list("outcome", "n"))
    avg_confidence = runs.filter(confidence__isnull=False).aggregate(v=Avg("confidence"))["v"]

    approvals = ApprovalQueue.objects.for_tenant(tenant).filter(created_at__gte=since)
    approval_status = dict(
        approvals.values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )
    decided = approvals.filter(reviewed_at__isnull=False)
    review_seconds = [
        (row["reviewed_at"] - row["created_at"]).total_seconds()
        for row in decided.values("reviewed_at", "created_at")[:1000]
    ]

    usage = LLMUsage.objects.for_tenant(tenant).filter(created_at__gte=since)
    tokens = usage.aggregate(
        input=Sum("input_tokens"),
        output=Sum("output_tokens"),
        cache_read=Sum("cache_read_input_tokens"),
        calls=Count("id"),
    )

    by_channel = list(
        messages.values(channel_type=F("conversation__channel__channel_type"))
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return {
        "days": [d.isoformat() for d in day_list],
        "messages": message_series,
        "messages_by_channel_type": {row["channel_type"]: row["n"] for row in by_channel},
        "ai_outcomes": {k: outcomes.get(k, 0) for k in AIReplyLog.Outcome.values},
        "ai_avg_confidence": round(avg_confidence, 3) if avg_confidence is not None else None,
        "approvals": {k: approval_status.get(k, 0) for k in ApprovalQueue.Status.values},
        "approval_median_minutes": (
            round(sorted(review_seconds)[len(review_seconds) // 2] / 60, 1)
            if review_seconds
            else None
        ),
        "llm": {k: v or 0 for k, v in tokens.items()},
    }


#: Audit actions shown in the activity feed, with their Vietnamese label.
ACTIVITY_LABELS = {
    "approval.created.draft": "AI soạn nháp chờ duyệt",
    "approval.created.escalation": "Cần người xử lý",
    "approval.created.aiva_outreach": "Sales AI gửi tin chăm sóc chờ duyệt",
    "approval.approved": "Đã duyệt và gửi",
    "approval.modified": "Đã sửa, duyệt và gửi",
    "approval.rejected": "Đã từ chối bản nháp",
    "approval.send_failed": "Gửi tin thất bại",
    "ai.auto_reply_sent": "AI tự trả lời",
    "ai.emergency_stop": "Dừng khẩn cấp AI",
    "ai.resumed": "Bật lại AI",
    "conversation.takeover": "Tiếp quản hội thoại",
    "conversation.takeover_auto": "Chủ tự trả lời trong Zalo, AI tạm im",
    "conversation.takeover_released": "Trả hội thoại cho AI",
    "message.sent_by_staff": "Nhân viên gửi tin",
    "reply.queued_for_owner": "Nháp chờ chủ gửi",
    "contact.consent_changed": "Thay đổi đồng ý liên hệ",
    "contact.crs_upserted": "CRS cập nhật khách hàng",
    "channel.connected": "Kết nối kênh",
    "channel.disconnected": "Ngắt kết nối kênh",
    "channel.personal_emergency_stop": "Dừng kênh Zalo cá nhân",
    "channel.personal_auto_stopped": "Kênh Zalo cá nhân tự dừng (dấu hiệu hạn chế)",
    "aiva.outbox_requeued": "Gửi lại sự kiện AIVA",
}


def activity(tenant, limit: int = 30) -> list[dict]:
    rows = (
        AuditLog.objects.for_tenant(tenant)
        .select_related("user")
        .order_by("-timestamp")[: max(1, min(limit, 100)) * 3]
    )
    items = []
    for row in rows:
        label = ACTIVITY_LABELS.get(row.action)
        if label is None and not row.action.startswith(("ai.", "approval.", "channel.")):
            continue
        items.append(
            {
                "id": row.pk,
                "action": row.action,
                "label": label or row.action,
                "object": row.model_name.split(".")[-1].lower() if row.model_name else "",
                "object_id": row.object_id,
                "user": row.user.get_username() if row.user else None,
                "at": row.timestamp.isoformat(),
            }
        )
        if len(items) >= limit:
            break
    return items
