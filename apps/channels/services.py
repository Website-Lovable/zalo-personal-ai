"""Channel connection lifecycle and the inbound event inbox.

- Zalo OA: OAuth v4 + PKCE connect, token refresh (single-use refresh tokens, row lock),
  contact sync.
- Zalo Bot: connect with a bot token (webhook or polling), disconnect, polling.
- Webhook inbox: store + dedupe raw events, process them in Celery.

Every connection change is written to the AuditLog. Tokens are never logged.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from common.audit import log_action

from .models import Channel, ChannelType, OAuthState, WebhookEvent
from .providers import provider_class_for
from .providers.http import ZaloAPIError, ZaloAuthError
from .providers.zalo_bot import ZaloBotClient, new_secret_token
from .providers.zalo_oa import authorize_url, exchange_code, new_pkce_pair, refresh_tokens

logger = logging.getLogger(__name__)


class ChannelConnectError(Exception):
    """User-facing connection problem (message is Vietnamese, safe to show)."""


def require_real_backend():
    if settings.ZALO_CHANNEL_BACKEND != "real":
        raise ChannelConnectError(
            "Hệ thống đang ở chế độ giả lập (ZALO_CHANNEL_BACKEND=mock), không gọi Zalo thật."
        )


def public_url(path: str) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    if not base:
        raise ChannelConnectError("Chưa cấu hình PUBLIC_BASE_URL (địa chỉ HTTPS công khai).")
    return f"{base}{path}"


def webhook_url(channel: Channel) -> str:
    name = "webhook-zalo-oa-channel" if channel.channel_type == "zalo_oa" else "webhook-zalo-bot"
    return public_url(reverse(name, kwargs={"key": channel.webhook_key}))


def _mark_connected(channel: Channel, **fields):
    for key, value in fields.items():
        setattr(channel, key, value)
    channel.status = Channel.Status.ACTIVE
    channel.last_error = ""
    channel.connected_at = timezone.now()
    channel.save()


def _mark_disconnected(channel: Channel, error: str):
    Channel.objects.filter(pk=channel.pk).update(
        status=Channel.Status.DISCONNECTED, last_error=error[:500], updated_at=timezone.now()
    )
    channel.status = Channel.Status.DISCONNECTED
    channel.last_error = error[:500]


# --- Zalo OA: OAuth ---------------------------------------------------------------------


def start_oa_oauth(channel: Channel, user) -> str:
    require_real_backend()
    if channel.channel_type != ChannelType.ZALO_OA:
        raise ChannelConnectError("Chỉ kênh Zalo OA mới kết nối bằng OAuth.")
    if not (settings.ZALO_OA_APP_ID and settings.ZALO_OA_APP_SECRET):
        raise ChannelConnectError("Chưa cấu hình ZALO_OA_APP_ID / ZALO_OA_APP_SECRET.")
    if not settings.ZALO_OA_OAUTH_REDIRECT_URI:
        raise ChannelConnectError("Chưa cấu hình ZALO_OA_OAUTH_REDIRECT_URI.")
    verifier, challenge = new_pkce_pair()
    state = OAuthState.objects.create(
        tenant=channel.tenant,
        channel=channel,
        state=secrets.token_urlsafe(32),
        code_verifier=verifier,
        created_by=user,
        expires_at=timezone.now() + timedelta(seconds=settings.ZALO_OA_OAUTH_STATE_TTL_SECONDS),
        data_origin=channel.data_origin,
    )
    log_action("channel.oauth_started", obj=channel, user=user)
    return authorize_url(state=state.state, code_challenge=challenge)


def complete_oa_oauth(*, state: str, code: str, oa_id: str) -> Channel:
    """Handle the OAuth callback. ``state`` binds the callback to the tenant that started it."""
    with transaction.atomic():
        pending = (
            OAuthState.objects.select_for_update()
            .select_related("channel")
            .filter(state=state, used_at__isnull=True)
            .first()
        )
        if pending is None or pending.expires_at < timezone.now():
            raise ChannelConnectError("Phiên kết nối không hợp lệ hoặc đã hết hạn. Hãy thử lại.")
        pending.used_at = timezone.now()
        pending.save(update_fields=["used_at", "updated_at"])
    channel = pending.channel
    if not code:
        raise ChannelConnectError(
            "Zalo không trả về mã xác thực (có thể bạn đã từ chối cấp quyền)."
        )
    clash = (
        Channel.objects.filter(channel_type=ChannelType.ZALO_OA, zalo_oa_id=oa_id, is_active=True)
        .exclude(pk=channel.pk)
        .exists()
    )
    if oa_id and clash:
        raise ChannelConnectError("OA này đã được kết nối với một kênh khác.")
    try:
        tokens = exchange_code(code, pending.code_verifier)
    except ZaloAPIError as exc:
        log_action(
            "channel.oauth_failed", obj=channel, user=pending.created_by, changes={"code": exc.code}
        )
        raise ChannelConnectError("Không đổi được mã xác thực lấy token từ Zalo.") from exc
    _mark_connected(
        channel,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_expires_at=tokens.expires_at,
        zalo_oa_id=oa_id or channel.zalo_oa_id,
        zalo_app_id=channel.zalo_app_id or settings.ZALO_OA_APP_ID,
    )
    log_action("channel.connected", obj=channel, user=pending.created_by, changes={"oa_id": oa_id})
    return channel


# --- Zalo OA: tokens ---------------------------------------------------------------------


def _token_needs_refresh(channel: Channel) -> bool:
    if not channel.token_expires_at:
        return False
    margin = timedelta(seconds=settings.ZALO_OA_TOKEN_REFRESH_MARGIN_SECONDS)
    return channel.token_expires_at - margin <= timezone.now()


def ensure_fresh_oa_token(channel: Channel) -> str:
    if not channel.access_token:
        raise ZaloAuthError("Channel has no access token; connect it first")
    if _token_needs_refresh(channel):
        refresh_oa_token(channel)
    return channel.access_token


def refresh_oa_token(channel: Channel, *, force: bool = False) -> Channel:
    """Refresh under a row lock: Zalo refresh tokens are single use, so two workers must
    never spend the same one. Updates ``channel`` in place."""
    error: ZaloAPIError | None = None
    with transaction.atomic():
        locked = Channel.objects.select_for_update().get(pk=channel.pk)
        if not force and not _token_needs_refresh(locked):
            fresh = locked  # another worker refreshed it already
        else:
            try:
                tokens = refresh_tokens(locked.refresh_token)
            except ZaloAPIError as exc:
                error = exc
            else:
                locked.access_token = tokens.access_token
                locked.refresh_token = tokens.refresh_token or locked.refresh_token
                locked.token_expires_at = tokens.expires_at
                locked.last_error = ""
                locked.save(
                    update_fields=[
                        "access_token",
                        "refresh_token",
                        "token_expires_at",
                        "last_error",
                        "updated_at",
                    ]
                )
                log_action("channel.token_refreshed", obj=locked)
                fresh = locked
    if error is not None:
        _mark_disconnected(channel, f"Làm mới token thất bại: {error}")
        log_action("channel.token_refresh_failed", obj=channel, changes={"code": error.code})
        raise ZaloAuthError(str(error), code=error.code) from error
    channel.access_token = fresh.access_token
    channel.refresh_token = fresh.refresh_token
    channel.token_expires_at = fresh.token_expires_at
    return channel


def refresh_expiring_oa_tokens() -> dict[str, int]:
    """Beat job: refresh every active OA channel whose token expires within the margin."""
    deadline = timezone.now() + timedelta(seconds=settings.ZALO_OA_TOKEN_REFRESH_MARGIN_SECONDS)
    channels = Channel.objects.filter(
        channel_type=ChannelType.ZALO_OA,
        is_active=True,
        status=Channel.Status.ACTIVE,
        token_expires_at__lte=deadline,
    )
    result = {"refreshed": 0, "failed": 0}
    for channel in channels:
        try:
            refresh_oa_token(channel)
            result["refreshed"] += 1
        except ZaloAPIError:
            result["failed"] += 1
    return result


# --- Zalo OA: contact sync ---------------------------------------------------------------


def sync_oa_contacts(channel: Channel) -> dict[str, int]:
    """Copy the OA's followers into Contacts (name/avatar/follower only).

    Never grants consent and never touches CRS-owned fields (CRS is the source of truth).
    """

    require_real_backend()
    provider = provider_class_for(channel.channel_type)(channel)
    if not hasattr(provider, "list_user_ids"):
        raise ChannelConnectError("Loại kênh này không hỗ trợ đồng bộ danh bạ.")
    page_size = settings.ZALO_OA_SYNC_PAGE_SIZE
    stats = {"created": 0, "updated": 0, "failed": 0}
    offset = 0
    while offset < settings.ZALO_OA_SYNC_MAX_CONTACTS:
        user_ids, total = provider.list_user_ids(offset=offset, count=page_size)
        for user_id in user_ids:
            try:
                detail = provider.get_user_detail(user_id)
            except ZaloAPIError:
                stats["failed"] += 1
                continue
            _, created = upsert_contact_from_zalo(channel, user_id, detail)
            stats["created" if created else "updated"] += 1
        offset += page_size
        if not user_ids or offset >= total:
            break
    log_action("channel.contacts_synced", obj=channel, changes=stats)
    return stats


def upsert_contact_from_zalo(channel: Channel, user_id: str, detail: dict):
    from apps.contacts.models import Contact

    avatar = detail.get("avatar") or (detail.get("avatars") or {}).get("240") or ""
    values = {
        "display_name": (detail.get("display_name") or detail.get("user_alias") or user_id)[:255],
        "avatar_url": avatar if str(avatar).startswith("http") else "",
        "last_synced_at": timezone.now(),
    }
    if "user_is_follower" in detail:
        values["is_follower"] = bool(detail["user_is_follower"])
    contact, created = Contact.objects.get_or_create(
        tenant=channel.tenant,
        channel=channel,
        zalo_user_id=user_id,
        defaults={**values, "data_origin": channel.data_origin},
    )
    if not created:
        for key, value in values.items():
            setattr(contact, key, value)
        contact.save(update_fields=[*values.keys(), "updated_at"])
    return contact, created


# --- Zalo Bot ----------------------------------------------------------------------------


@dataclass
class BotConnection:
    channel: Channel
    bot: dict


def connect_bot(channel: Channel, *, bot_token: str, delivery: str, user) -> BotConnection:
    require_real_backend()
    if channel.channel_type != ChannelType.ZALO_BOT:
        raise ChannelConnectError("Chỉ áp dụng cho kênh Zalo Bot.")
    client = ZaloBotClient(bot_token)
    try:
        bot = client.get_me()
        if delivery == "webhook":
            secret = new_secret_token()
            channel.webhook_secret = secret  # needed by webhook_url() callers below
            client.set_webhook(webhook_url(channel), secret)
        else:
            secret = ""
            client.delete_webhook()
    except ZaloAPIError as exc:
        log_action("channel.connect_failed", obj=channel, user=user, changes={"code": exc.code})
        raise ChannelConnectError(f"Zalo Bot từ chối kết nối: {exc}") from exc
    config = {**channel.config, "delivery": delivery, "bot_id": str(bot.get("id", ""))}
    config["bot_name"] = str(bot.get("account_name", ""))
    _mark_connected(channel, access_token=bot_token, webhook_secret=secret, config=config)
    log_action(
        "channel.connected",
        obj=channel,
        user=user,
        changes={"delivery": delivery, "bot_id": config["bot_id"]},
    )
    return BotConnection(channel=channel, bot=bot)


def disconnect_channel(channel: Channel, user) -> Channel:
    if channel.channel_type == ChannelType.ZALO_BOT and channel.access_token:
        if settings.ZALO_CHANNEL_BACKEND == "real":
            try:
                ZaloBotClient(channel.access_token).delete_webhook()
            except ZaloAPIError:
                logger.warning("deleteWebhook failed for channel %s", channel.pk)
    channel.access_token = ""
    channel.refresh_token = ""
    channel.webhook_secret = ""
    channel.token_expires_at = None
    channel.status = Channel.Status.DISCONNECTED
    channel.save()
    log_action("channel.disconnected", obj=channel, user=user)
    return channel


def poll_bot_once(channel: Channel, *, timeout: int | None = None) -> int:
    """Fetch pending updates (polling delivery) and put them in the inbox. Returns count."""
    require_real_backend()
    client = ZaloBotClient(channel.access_token)
    updates = client.get_updates(timeout or settings.ZALO_BOT_POLL_TIMEOUT_SECONDS)
    stored = 0
    for update in updates:
        if record_webhook_event(channel, update, source="polling") is not None:
            stored += 1
    return stored


# --- Inbox: store fast, process async ------------------------------------------------------


def record_webhook_event(channel: Channel, payload: dict, *, source: str = "webhook"):
    """Store a verified raw event and schedule processing. Returns None for duplicates.

    One payload carries one Zalo event (OA and Bot both send one event per request).
    """
    provider = provider_class_for(channel.channel_type)(channel)
    events = provider.parse_events(payload)
    first = events[0] if events else None
    dedupe_key = first.dedupe_key if first else f"empty:{timezone.now().timestamp()}"
    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                tenant=channel.tenant,
                channel=channel,
                source=source,
                event_name=(first.event_name if first else "")[:100],
                dedupe_key=dedupe_key[:255],
                payload=payload,
                data_origin=channel.data_origin,
            )
    except IntegrityError:
        return None  # Zalo retried a delivery we already have
    from .tasks import process_webhook_event

    transaction.on_commit(lambda: process_webhook_event.delay(event.pk))
    return event


def process_event(event: WebhookEvent) -> WebhookEvent:
    """Turn a stored event into contacts / conversations / messages. Idempotent."""
    from apps.conversations.inbound import handle_channel_event

    if event.status == WebhookEvent.Status.PROCESSED:
        return event
    channel = event.channel
    event.attempts += 1
    try:
        provider = provider_class_for(channel.channel_type)(channel)
        handled = False
        with transaction.atomic():
            for normalized in provider.parse_events(event.payload):
                handled = handle_channel_event(channel, normalized) or handled
        event.status = WebhookEvent.Status.PROCESSED if handled else WebhookEvent.Status.IGNORED
        event.error = ""
        event.processed_at = timezone.now()
    except Exception as exc:  # noqa: BLE001 - recorded on the event and retried by Celery
        logger.exception("Processing webhook event %s failed", event.pk)
        event.status = WebhookEvent.Status.FAILED
        event.error = f"{exc.__class__.__name__}: {exc}"[:2000]
    event.save(update_fields=["status", "attempts", "error", "processed_at", "updated_at"])
    return event
