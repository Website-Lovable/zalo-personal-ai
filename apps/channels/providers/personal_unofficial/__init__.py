"""ISOLATED provider for personal Zalo accounts (unofficial login).

Rules (Blueprint B2, Kickoff 3): disabled by default via ZALO_PERSONAL_UNOFFICIAL_ENABLED,
owner's own account only (QR scanned by the owner), written risk acknowledgement before
login, reply only to people who wrote first, rate limits + time window + daily cap, emergency
stop, auto-stop when Zalo shows signs of restricting the account.

Login library (approved by the project owner on 26/11/2026): zca-js 2.4.0 (MIT), run in the
Node sidecar under ``sidecar/zalo-personal/``. Django never imports it; it talks to the
sidecar through ``adapter.SidecarAdapter`` (HMAC-signed HTTP).

Removing this feature: delete this package, ``sidecar/zalo-personal/``, the ``zalo-personal``
service in docker-compose and the two marked lines in ``config/urls.py``. The core, OA, Bot
and AIVA integration do not import it.
"""

from .provider import PersonalUnofficialProvider

__all__ = ["PersonalUnofficialProvider"]
