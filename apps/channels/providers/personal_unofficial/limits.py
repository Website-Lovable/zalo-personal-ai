"""Send guard for personal Zalo channels (Blueprint B2 #3-#5, Kickoff 3 #3 and #5).

Every automatic send on a ``zalo_personal`` channel passes ``check_send`` first. It refuses:
- when the feature flag, the global emergency stop or the channel's own stop is on;
- when the channel is not ``active`` or the owner's risk acknowledgement is missing;
- when the recipient has not written first, or last wrote longer ago than the reply window
  (we only reply, never start a conversation);
- outside the allowed hours, above the per-minute / per-hour / per-day / per-contact caps,
  sooner than the minimum gap since the previous send, or faster than a person would reply.

Counts come from the ``Message`` table (messages this app sent itself), so they survive
restarts and are shared by every worker. Callers must hold the channel row lock
(``select_for_update``) so two workers cannot both pass the last free slot.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

#: Outbound messages created by ``dispatch_reply`` use this idempotency key prefix. Echoes of
#: messages the owner typed on their phone do not, so they do not use up the AI's quota.
APP_SENT_PREFIX = "out-"


@dataclass(frozen=True)
class Limits:
    window_start: time
    window_end: time
    per_minute: int
    per_hour: int
    per_day: int
    per_contact_per_hour: int
    min_gap_seconds: int
    min_reply_delay_seconds: int
    reply_window_hours: int

    @classmethod
    def for_channel(cls, channel) -> Limits:
        """Global limits from settings; a channel's ``config["limits"]`` may only tighten them."""
        start, end = parse_window(settings.ZALO_PERSONAL_SEND_WINDOW)
        base = cls(
            window_start=start,
            window_end=end,
            per_minute=settings.ZALO_PERSONAL_MAX_PER_MINUTE,
            per_hour=settings.ZALO_PERSONAL_MAX_PER_HOUR,
            per_day=settings.ZALO_PERSONAL_MAX_PER_DAY,
            per_contact_per_hour=settings.ZALO_PERSONAL_MAX_PER_CONTACT_PER_HOUR,
            min_gap_seconds=settings.ZALO_PERSONAL_MIN_GAP_SECONDS,
            min_reply_delay_seconds=settings.ZALO_PERSONAL_MIN_REPLY_DELAY_SECONDS,
            reply_window_hours=settings.ZALO_PERSONAL_REPLY_WINDOW_HOURS,
        )
        overrides = (channel.config or {}).get("limits") or {}
        values = dict(base.__dict__)
        for key in ("per_minute", "per_hour", "per_day", "per_contact_per_hour"):
            if isinstance(overrides.get(key), int) and overrides[key] >= 0:
                values[key] = min(values[key], overrides[key])
        for key in ("min_gap_seconds", "min_reply_delay_seconds"):
            if isinstance(overrides.get(key), int):
                values[key] = max(values[key], overrides[key])
        if (
            isinstance(overrides.get("reply_window_hours"), int)
            and overrides["reply_window_hours"] >= 0
        ):
            values["reply_window_hours"] = min(
                values["reply_window_hours"], overrides["reply_window_hours"]
            )
        return cls(**values)


def parse_window(value: str) -> tuple[time, time]:
    """``"07:30-21:30"`` -> (07:30, 21:30). Local time (settings.TIME_ZONE).

    A window may cross midnight (``"20:00-02:00"``); equal ends mean no restriction.
    """
    try:
        start_s, end_s = value.split("-")
        start = time.fromisoformat(start_s.strip())
        end = time.fromisoformat(end_s.strip())
    except ValueError as exc:
        raise ImproperlyConfigured(f"Invalid ZALO_PERSONAL_SEND_WINDOW: {value!r}") from exc
    return start, end


def in_window(now_local: datetime, start: time, end: time) -> bool:
    current = now_local.time()
    if start == end:
        return True  # "00:00-00:00": no time restriction
    if start < end:
        return start <= current < end
    return current >= start or current < end  # window across midnight


@dataclass
class GuardDecision:
    allowed: bool
    reason: str = ""  # machine code, e.g. "rate_minute"
    message: str = ""  # Vietnamese, safe to show to the owner
    retry_after_seconds: int | None = None
    usage: dict = field(default_factory=dict)


def _deny(reason, message, retry_after=None, usage=None) -> GuardDecision:
    return GuardDecision(
        allowed=False,
        reason=reason,
        message=message,
        retry_after_seconds=int(retry_after) if retry_after is not None else None,
        usage=usage or {},
    )


def stop_state(channel) -> GuardDecision | None:
    """Switch checks shared by sending and the status endpoint."""
    if not settings.ZALO_PERSONAL_UNOFFICIAL_ENABLED:
        return _deny("feature_disabled", "Tính năng Zalo cá nhân đang tắt trên hệ thống.")
    if settings.ZALO_PERSONAL_EMERGENCY_STOP:
        return _deny("global_stop", "Toàn hệ thống đang dừng khẩn cấp kênh Zalo cá nhân.")
    if (channel.config or {}).get("emergency_stop"):
        return _deny("channel_stop", "Kênh đang bị dừng khẩn cấp.")
    if not channel.is_active or channel.status != channel.Status.ACTIVE:
        return _deny("channel_not_active", "Kênh chưa kết nối hoặc đang tạm dừng/bị hạn chế.")
    if not channel.risk_acknowledged:
        return _deny("risk_not_acknowledged", "Chủ tài khoản chưa xác nhận rủi ro.")
    return None


def usage_counts(channel, now=None) -> dict[str, int]:
    from apps.conversations.models import Message

    now = now or timezone.now()
    local_midnight = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    sent = Message.objects.filter(
        tenant=channel.tenant,
        conversation__channel=channel,
        direction=Message.Direction.OUTBOUND,
        idempotency_key__startswith=APP_SENT_PREFIX,
    )
    return {
        "minute": sent.filter(created_at__gte=now - timedelta(minutes=1)).count(),
        "hour": sent.filter(created_at__gte=now - timedelta(hours=1)).count(),
        "day": sent.filter(created_at__gte=local_midnight).count(),
    }


def check_send(channel, contact, now=None) -> GuardDecision:
    from apps.conversations.models import Message

    now = now or timezone.now()
    stopped = stop_state(channel)
    if stopped is not None:
        return stopped
    foreign = contact is not None and (
        contact.channel_id != channel.pk or contact.tenant_id != channel.tenant_id
    )
    if contact is None or foreign:
        return _deny("unknown_contact", "Không tìm thấy người nhận trong kênh này.")
    if not contact.can_receive_reply():
        return _deny("not_allowed", "Người này chưa nhắn trước hoặc không cho phép trả lời.")

    limits = Limits.for_channel(channel)
    last_inbound = (
        Message.objects.filter(
            tenant=channel.tenant,
            conversation__channel=channel,
            conversation__contact=contact,
            direction=Message.Direction.INBOUND,
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if last_inbound is None:
        return _deny("no_inbound", "Chỉ trả lời người đã nhắn trước.")
    if now - last_inbound > timedelta(hours=limits.reply_window_hours):
        return _deny(
            "reply_window_passed",
            f"Tin nhắn gần nhất của khách đã quá {limits.reply_window_hours} giờ; "
            "không chủ động nhắn lại.",
        )
    too_soon = limits.min_reply_delay_seconds - (now - last_inbound).total_seconds()
    if too_soon > 0:
        return _deny("too_fast", "Chờ thêm để trả lời giống người.", retry_after=too_soon + 1)

    local_now = timezone.localtime(now)
    if not in_window(local_now, limits.window_start, limits.window_end):
        return _deny(
            "outside_hours",
            f"Ngoài khung giờ gửi {limits.window_start:%H:%M}-{limits.window_end:%H:%M}.",
            retry_after=_seconds_until(local_now, limits.window_start),
        )

    counts = usage_counts(channel, now)
    usage = {
        **counts,
        "limits": {"minute": limits.per_minute, "hour": limits.per_hour, "day": limits.per_day},
    }
    if counts["day"] >= limits.per_day:
        tomorrow = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        return _deny(
            "rate_day",
            f"Đã đạt trần {limits.per_day} tin/ngày.",
            retry_after=(tomorrow - local_now).total_seconds(),
            usage=usage,
        )
    if counts["hour"] >= limits.per_hour:
        return _deny("rate_hour", f"Đã đạt trần {limits.per_hour} tin/giờ.", 300, usage)
    if counts["minute"] >= limits.per_minute:
        return _deny("rate_minute", f"Đã đạt trần {limits.per_minute} tin/phút.", 60, usage)

    sent_to_contact = Message.objects.filter(
        tenant=channel.tenant,
        conversation__channel=channel,
        conversation__contact=contact,
        direction=Message.Direction.OUTBOUND,
        idempotency_key__startswith=APP_SENT_PREFIX,
        created_at__gte=now - timedelta(hours=1),
    ).count()
    if sent_to_contact >= limits.per_contact_per_hour:
        return _deny(
            "rate_contact",
            f"Đã gửi {limits.per_contact_per_hour} tin/giờ cho người này.",
            600,
            usage,
        )

    last_sent = (
        Message.objects.filter(
            tenant=channel.tenant,
            conversation__channel=channel,
            direction=Message.Direction.OUTBOUND,
            idempotency_key__startswith=APP_SENT_PREFIX,
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    marker = (channel.config or {}).get("last_sent_at")
    if marker:
        try:
            marked = datetime.fromisoformat(marker)
        except ValueError:
            marked = None
        if marked is not None and (last_sent is None or marked > last_sent):
            last_sent = marked
    if last_sent is not None:
        wait = limits.min_gap_seconds - (now - last_sent).total_seconds()
        if wait > 0:
            return _deny("min_gap", "Giãn cách giữa hai tin gửi.", wait + 1, usage)
    return GuardDecision(allowed=True, usage=usage)


def _seconds_until(local_now: datetime, start: time) -> int:
    target = local_now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    return int((target - local_now).total_seconds())


def human_delay_seconds(text: str, rng: random.Random | None = None) -> float:
    """Suggested wait before sending ``text`` (reading + typing time, with jitter).

    The Phase 3 pipeline uses it as the Celery countdown for automatic replies on this
    channel type; the guard's ``min_reply_delay_seconds`` is the hard floor.
    """
    rng = rng or random.Random()  # noqa: S311 - not security sensitive
    typing = len(text or "") / max(1, settings.ZALO_PERSONAL_TYPING_CHARS_PER_SECOND)
    delay = settings.ZALO_PERSONAL_MIN_REPLY_DELAY_SECONDS + typing
    delay *= rng.uniform(0.8, 1.3)
    return round(min(delay, settings.ZALO_PERSONAL_MAX_REPLY_DELAY_SECONDS), 1)
