"""Run getUpdates in a loop for Zalo Bot channels in polling delivery (dev without HTTPS)."""

import time

from django.core.management.base import BaseCommand

from apps.channels.models import Channel, ChannelType
from apps.channels.providers.http import ZaloAPIError
from apps.channels.services import poll_bot_once


class Command(BaseCommand):
    help = "Poll Zalo Bot updates (getUpdates) for bots in polling delivery mode."

    def add_arguments(self, parser):
        parser.add_argument("--channel", type=int, help="Only this channel id")
        parser.add_argument("--once", action="store_true", help="Poll once and exit")

    def handle(self, *args, **options):
        qs = Channel.objects.filter(
            channel_type=ChannelType.ZALO_BOT, is_active=True, config__delivery="polling"
        )
        if options["channel"]:
            qs = qs.filter(pk=options["channel"])
        while True:
            for channel in qs:
                try:
                    count = poll_bot_once(channel)
                except ZaloAPIError as exc:
                    self.stderr.write(f"channel {channel.pk}: {exc}")
                    continue
                if count:
                    self.stdout.write(f"channel {channel.pk}: {count} update(s)")
            if options["once"]:
                return
            time.sleep(1)
