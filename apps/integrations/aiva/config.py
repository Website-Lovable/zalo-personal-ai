from django.conf import settings

OFF, SANDBOX, LIVE = "off", "sandbox", "live"
SCHEMA_VERSION = "1.0"


def get_mode() -> str:
    return settings.AIVA_INTEGRATION_MODE


def is_enabled() -> bool:
    return get_mode() != OFF


def allowed_data_origins() -> set[str]:
    """sandbox only ever carries demo data; live carries real data (needs owner approval)."""
    mode = get_mode()
    if mode == SANDBOX:
        return {"demo"}
    if mode == LIVE:
        return {"real"}
    return set()
