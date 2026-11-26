"""HMAC-SHA256 request signing (Kickoff 2.4).

Signed string: ``f"{timestamp}.{raw_body}"``. Headers: ``X-Signature: sha256=<hex>`` and
``X-Timestamp: <unix seconds>``. Requests older/newer than the tolerance (default ±5 min)
are rejected to stop replays.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-Signature"
TIMESTAMP_HEADER = "X-Timestamp"
PREFIX = "sha256="


class SignatureError(Exception):
    pass


def compute_signature(secret: str, timestamp: int | str, body: bytes) -> str:
    if not secret:
        raise SignatureError("Signing secret is empty")
    message = f"{timestamp}.".encode() + body
    return PREFIX + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign_headers(secret: str, body: bytes, *, now: float | None = None) -> dict[str, str]:
    timestamp = int(now if now is not None else time.time())
    return {
        SIGNATURE_HEADER: compute_signature(secret, timestamp, body),
        TIMESTAMP_HEADER: str(timestamp),
    }


def verify_signature(
    secret: str,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> None:
    """Raise SignatureError unless the signature and timestamp are valid."""
    if not signature or not timestamp:
        raise SignatureError("Missing signature headers")
    if not signature.startswith(PREFIX):
        raise SignatureError("Unsupported signature scheme")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("Invalid timestamp") from exc
    current = now if now is not None else time.time()
    if abs(current - ts) > tolerance_seconds:
        raise SignatureError("Timestamp outside tolerance (possible replay)")
    expected = compute_signature(secret, ts, body)
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("Signature mismatch")
