"""WebSocket consumer for /ws/notifications/."""

from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .notifications import read_ticket, tenant_group


@database_sync_to_async
def _member(user_id: int, tenant_id: int):
    from apps.accounts.models import User

    return (
        User.objects.filter(
            pk=user_id, tenant_id=tenant_id, is_active=True, tenant__is_active=True
        ).first()
        is not None
    )


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    group = None

    async def connect(self):
        query = parse_qs(self.scope.get("query_string", b"").decode())
        claims = read_ticket((query.get("ticket") or [""])[0])
        if not claims or not await _member(claims["u"], claims["t"]):
            await self.close(code=4401)
            return
        self.group = tenant_group(claims["t"])
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "hello"})

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def notify(self, message):
        await self.send_json(message["event"])
