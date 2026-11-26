"""Provider registry.

``get_provider(channel)`` returns the provider for a channel. With
``ZALO_CHANNEL_BACKEND=mock`` (default in dev/test) every channel uses the in-memory mock, so
nothing is sent to Zalo. ``zalo_personal`` is refused while its feature flag is off, even in
mock mode.
"""

from __future__ import annotations

from django.conf import settings

from common.exceptions import FeatureDisabledError

from .base import ChannelProvider, InboundMessage, SendResult
from .mock import MockChannelProvider

__all__ = ["ChannelProvider", "InboundMessage", "SendResult", "get_provider", "provider_class_for"]


def provider_class_for(channel_type: str) -> type[ChannelProvider]:
    if channel_type == "zalo_oa":
        from .zalo_oa import ZaloOAProvider

        return ZaloOAProvider
    if channel_type == "zalo_bot":
        from .zalo_bot import ZaloBotProvider

        return ZaloBotProvider
    if channel_type == "zalo_personal":
        if not settings.ZALO_PERSONAL_UNOFFICIAL_ENABLED:
            raise FeatureDisabledError("ZALO_PERSONAL_UNOFFICIAL_ENABLED is off")
        from .personal_unofficial import PersonalUnofficialProvider

        return PersonalUnofficialProvider
    raise ValueError(f"Unknown channel type: {channel_type}")


def get_provider(channel) -> ChannelProvider:
    real_class = provider_class_for(channel.channel_type)  # also enforces feature flags
    if settings.ZALO_CHANNEL_BACKEND == "mock":
        return MockChannelProvider(channel)
    return real_class(channel)
