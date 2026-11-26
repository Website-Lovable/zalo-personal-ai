"""Feature flag and settings access for the AIVA integration (Kickoff 2.4).

AIVA_INTEGRATION_MODE = off | sandbox | live
- off:     nothing leaves the app, inbound integration endpoints answer 503.
- sandbox: only data_origin=demo travels in either direction.
- live:    only data_origin=real; refused unless AIVA_LIVE_APPROVED=true (owner approval,
           Phase 6).
"""

from django.conf import settings

from common.exceptions import FeatureDisabledError

OFF, SANDBOX, LIVE = "off", "sandbox", "live"
SCHEMA_VERSION = "1.0"


def get_mode() -> str:
    return settings.AIVA_INTEGRATION_MODE


def is_enabled() -> bool:
    return get_mode() != OFF


def live_approved() -> bool:
    return bool(getattr(settings, "AIVA_LIVE_APPROVED", False))


def ensure_mode_usable() -> None:
    """Raise unless the current mode may exchange data right now."""
    mode = get_mode()
    if mode == OFF:
        raise FeatureDisabledError("AIVA_INTEGRATION_MODE=off: no exchange with AIVA")
    if mode == LIVE and not live_approved():
        raise FeatureDisabledError("AIVA live mode needs owner approval (AIVA_LIVE_APPROVED)")


def allowed_data_origins() -> set[str]:
    """sandbox only ever carries demo data; live carries real data (needs owner approval)."""
    mode = get_mode()
    if mode == SANDBOX:
        return {"demo"}
    if mode == LIVE:
        return {"real"}
    return set()


def client_backend() -> str:
    """``http`` (default) or ``mock`` (sandbox only, for development without AIVA)."""
    return getattr(settings, "AIVA_CLIENT", "http")
