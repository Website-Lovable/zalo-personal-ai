"""Lifecycle of a personal Zalo channel: QR login, emergency stop, resume, auto-stop.

Every state change is written to the AuditLog. The session string is never logged.
"""

from __future__ import annotations

import logging
from datetime import datetime

import django.dispatch
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from common.audit import log_action
from common.exceptions import FeatureDisabledError

from .adapter import LoginState, RestrictionDetected, get_adapter

logger = logging.getLogger(__name__)

#: Sent when a personal channel stops by itself. kwargs: channel, kind, detail.
personal_channel_auto_stopped = django.dispatch.Signal()


class PersonalChannelError(Exception):
    """User-facing problem (Vietnamese message, safe to show)."""


def _require_enabled():
    if not settings.ZALO_PERSONAL_UNOFFICIAL_ENABLED:
        raise FeatureDisabledError("ZALO_PERSONAL_UNOFFICIAL_ENABLED is off")


def _require_personal(channel):
    if not channel.is_personal:
        raise PersonalChannelError("Chỉ áp dụng cho kênh Zalo cá nhân.")


def _save_config(channel, **changes):
    config = dict(channel.config or {})
    for key, value in changes.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    channel.config = config


# --- QR login ------------------------------------------------------------------------------


def start_login(channel, user) -> dict:
    """Start a QR login. The owner scans it with the Zalo app on their own phone."""
    _require_enabled()
    _require_personal(channel)
    if not channel.risk_acknowledged:
        raise PersonalChannelError("Cần xác nhận rủi ro bằng văn bản trước khi đăng nhập.")
    if settings.ZALO_PERSONAL_EMERGENCY_STOP:
        raise PersonalChannelError("Toàn hệ thống đang dừng khẩn cấp kênh Zalo cá nhân.")
    challenge = get_adapter().start_login(channel)
    _save_config(
        channel,
        pending_login={
            "login_id": challenge.login_id,
            "expires_at": challenge.expires_at.isoformat(),
            "started_by": user.pk,
        },
    )
    channel.save(update_fields=["config", "updated_at"])
    log_action("channel.personal_login_started", obj=channel, user=user)
    return {
        "login_id": challenge.login_id,
        "qr_image": challenge.qr_image,
        "expires_at": challenge.expires_at,
    }


def poll_login(channel, user, login_id: str) -> dict:
    _require_enabled()
    _require_personal(channel)
    pending = (channel.config or {}).get("pending_login") or {}
    if not login_id or pending.get("login_id") != login_id:
        raise PersonalChannelError("Phiên đăng nhập không hợp lệ. Hãy tạo mã QR mới.")
    if datetime.fromisoformat(pending["expires_at"]) < timezone.now():
        _save_config(channel, pending_login=None)
        channel.save(update_fields=["config", "updated_at"])
        return {"state": LoginState.EXPIRED}
    status = get_adapter().login_status(channel, login_id)
    if status.state in (LoginState.EXPIRED, LoginState.FAILED):
        _save_config(channel, pending_login=None)
        channel.save(update_fields=["config", "updated_at"])
        log_action(
            "channel.personal_login_failed",
            obj=channel,
            user=user,
            changes={"state": status.state, "error": status.error[:200]},
        )
        return {"state": status.state}
    if status.state != LoginState.CONFIRMED:
        return {"state": status.state}
    _complete_login(channel, user, status)
    return {
        "state": LoginState.CONFIRMED,
        "account_id": status.account_id,
        "display_name": status.display_name,
    }


def _complete_login(channel, user, status):
    from apps.channels.models import Channel

    clash = (
        Channel.objects.filter(
            channel_type=channel.channel_type,
            is_active=True,
            config__zalo_account_id=status.account_id,
        )
        .exclude(pk=channel.pk)
        .exists()
    )
    if clash:
        get_adapter().logout(channel)
        _save_config(channel, pending_login=None)
        channel.save(update_fields=["config", "updated_at"])
        log_action(
            "channel.personal_login_rejected",
            obj=channel,
            user=user,
            changes={"reason": "account_already_connected"},
        )
        raise PersonalChannelError("Tài khoản Zalo này đã được kết nối ở một kênh khác.")
    previous = (channel.config or {}).get("zalo_account_id")
    _save_config(
        channel,
        pending_login=None,
        zalo_account_id=status.account_id,
        zalo_display_name=status.display_name[:255],
        logged_in_by=user.pk,
        emergency_stop=None,
        auto_stopped=None,
        consecutive_failures=0,
    )
    channel.session_data = status.session
    channel.status = Channel.Status.ACTIVE
    channel.last_error = ""
    channel.connected_at = timezone.now()
    channel.save()
    log_action(
        "channel.personal_logged_in",
        obj=channel,
        user=user,
        changes={
            "zalo_account_id": status.account_id,
            "display_name": status.display_name,
            "account_changed": bool(previous and previous != status.account_id),
        },
    )


def logout(channel, user):
    _require_personal(channel)
    if settings.ZALO_PERSONAL_UNOFFICIAL_ENABLED and channel.session_data:
        try:
            get_adapter().logout(channel)
        except Exception:  # noqa: BLE001 - best effort, the local session is wiped anyway
            logger.warning("Sidecar logout failed for channel %s", channel.pk)
    channel.session_data = ""
    channel.status = channel.Status.DISCONNECTED
    _save_config(channel, pending_login=None)
    channel.save()
    log_action("channel.personal_logged_out", obj=channel, user=user)
    return channel


# --- Emergency stop / resume ---------------------------------------------------------------


def emergency_stop(channel, user, reason: str = ""):
    """Stop all sending on this channel immediately. Any admin or owner may press it."""
    _require_personal(channel)
    from apps.channels.models import Channel, ChannelMode

    with transaction.atomic():
        locked = Channel.objects.select_for_update().get(pk=channel.pk)
        if locked.status != Channel.Status.PAUSED:
            _save_config(locked, mode_before_stop=locked.mode)
        _save_config(
            locked,
            emergency_stop={
                "by": user.pk if user else None,
                "at": timezone.now().isoformat(),
                "reason": reason[:500],
            },
        )
        locked.status = Channel.Status.PAUSED
        locked.mode = ChannelMode.ASSIST
        locked.save()
        log_action(
            "channel.personal_emergency_stop", obj=locked, user=user, changes={"reason": reason}
        )
    channel.refresh_from_db()
    return channel


def resume(channel, user, *, restriction_reviewed: bool = False):
    """Owner only. Clears a stop; after an auto-stop the owner must confirm they checked."""
    _require_enabled()
    _require_personal(channel)
    from apps.channels.models import Channel

    config = channel.config or {}
    if config.get("auto_stopped") and not restriction_reviewed:
        raise PersonalChannelError(
            "Kênh tự dừng vì có dấu hiệu bị hạn chế. Hãy kiểm tra tài khoản Zalo trên điện "
            "thoại rồi xác nhận đã kiểm tra trước khi chạy lại."
        )
    if not channel.risk_acknowledged:
        raise PersonalChannelError("Cần xác nhận rủi ro bằng văn bản trước khi chạy lại.")
    if not channel.session_data:
        raise PersonalChannelError("Chưa đăng nhập. Hãy quét mã QR để đăng nhập lại.")
    if settings.ZALO_PERSONAL_EMERGENCY_STOP:
        raise PersonalChannelError("Toàn hệ thống đang dừng khẩn cấp kênh Zalo cá nhân.")
    previous_mode = config.get("mode_before_stop")
    _save_config(
        channel,
        emergency_stop=None,
        auto_stopped=None,
        consecutive_failures=0,
        mode_before_stop=None,
    )
    if previous_mode:
        channel.mode = previous_mode
    channel.status = Channel.Status.ACTIVE
    channel.last_error = ""
    channel.save()
    log_action(
        "channel.personal_resumed",
        obj=channel,
        user=user,
        changes={"restriction_reviewed": restriction_reviewed},
    )
    return channel


# --- Auto-stop ------------------------------------------------------------------------------


def auto_stop(channel, kind: str, detail: str = ""):
    """Stop the channel because Zalo shows signs of restricting it, and tell the owner."""
    from apps.channels.models import Channel, ChannelMode

    current = Channel.objects.filter(pk=channel.pk).values("mode", "status", "config").first()
    if current and current["status"] not in (Channel.Status.RESTRICTED, Channel.Status.PAUSED):
        config = dict(current["config"] or {})
        config["mode_before_stop"] = current["mode"]
        Channel.objects.filter(pk=channel.pk).update(config=config)
    Channel.objects.filter(pk=channel.pk).update(
        status=Channel.Status.RESTRICTED,
        mode=ChannelMode.ASSIST,
        last_error=f"Tự dừng: {kind}"[:500],
        updated_at=timezone.now(),
    )
    channel.refresh_from_db()
    _save_config(
        channel,
        auto_stopped={"kind": kind, "detail": detail[:500], "at": timezone.now().isoformat()},
    )
    Channel.objects.filter(pk=channel.pk).update(config=channel.config)
    log_action(
        "channel.personal_auto_stopped", obj=channel, changes={"kind": kind, "detail": detail}
    )
    logger.warning("Personal Zalo channel %s auto-stopped: %s", channel.pk, kind)
    personal_channel_auto_stopped.send(sender=Channel, channel=channel, kind=kind, detail=detail)
    _notify_owners(channel, kind)
    return channel


def _notify_owners(channel, kind: str):
    from apps.accounts.models import User

    emails = list(
        User.objects.filter(tenant=channel.tenant, role="owner", is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    if not emails:
        return
    try:
        send_mail(
            subject=f"[Zalo AI] Kênh '{channel.name}' đã tự dừng",
            message=(
                f"Kênh Zalo cá nhân '{channel.name}' đã tự dừng gửi tin vì phát hiện dấu hiệu "
                f"bị hạn chế ({kind}). Trợ lý đã chuyển sang chế độ soạn sẵn, không gửi tự "
                "động. Hãy kiểm tra tài khoản Zalo trên điện thoại, sau đó vào ứng dụng để "
                "chạy lại nếu mọi thứ bình thường."
            ),
            from_email=None,
            recipient_list=emails,
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001 - notification is best effort
        logger.warning("Could not email owners about channel %s", channel.pk)


def record_send_failure(channel, error: str):
    """Count consecutive failed sends; too many in a row is treated as a restriction sign."""
    from apps.channels.models import Channel

    with transaction.atomic():
        locked = Channel.objects.select_for_update().get(pk=channel.pk)
        failures = int((locked.config or {}).get("consecutive_failures", 0)) + 1
        _save_config(locked, consecutive_failures=failures)
        locked.save(update_fields=["config", "updated_at"])
    log_action(
        "channel.personal_send_failed",
        obj=channel,
        changes={"error": error[:200], "consecutive_failures": failures},
    )
    if failures >= settings.ZALO_PERSONAL_MAX_CONSECUTIVE_FAILURES:
        auto_stop(channel, "too_many_failures", error)
        return True
    return False


def record_send_success(channel):
    if (channel.config or {}).get("consecutive_failures"):
        from apps.channels.models import Channel

        _save_config(channel, consecutive_failures=0)
        Channel.objects.filter(pk=channel.pk).update(config=channel.config)


def handle_restriction(channel, exc: RestrictionDetected):
    return auto_stop(channel, exc.kind, exc.detail)


def handle_session_update(channel, *, account_id: str, session: str):
    """The sidecar refreshed the session cookies; store them (encrypted)."""
    known = (channel.config or {}).get("zalo_account_id")
    if known and account_id and account_id != known:
        auto_stop(channel, "account_changed", "session belongs to another Zalo account")
        return
    if session:
        channel.session_data = session
        channel.save(update_fields=["session_data", "updated_at"])
        log_action("channel.personal_session_refreshed", obj=channel)
