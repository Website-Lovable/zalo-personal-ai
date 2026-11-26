"""ISOLATED provider for personal Zalo accounts (unofficial login).

Rules (Blueprint B2, Kickoff 3): disabled by default via ZALO_PERSONAL_UNOFFICIAL_ENABLED,
owner's own account only (QR scanned by the owner), written risk acknowledgement, reply only
to people who wrote first, rate limits, emergency stop, auto-stop when restricted.

Phase 1 contains NO login code. The login library will be proposed (name, licence,
maintenance) and approved by the project owner in Phase 2b. This package can be deleted
without affecting the core, OA, Bot or the AIVA integration.
"""

from .provider import PersonalUnofficialProvider

__all__ = ["PersonalUnofficialProvider"]
