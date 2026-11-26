"""Envelope builder + validator for schema v1 (docs/integration/aiva-v1.schema.json)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Any

from django.conf import settings
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from .config import SCHEMA_VERSION

SCHEMA_PATH = settings.BASE_DIR / "docs" / "integration" / "aiva-v1.schema.json"
EVENT_TYPES = (
    "lead.inbound",
    "conversation.summary",
    "opportunity.suggested",
    "handoff.requested",
    "consent.changed",
    "profile.update_suggested",
)


class EnvelopeError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema(), format_checker=FormatChecker())


def validate_envelope(envelope: dict[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(envelope), key=lambda e: list(e.path))
    if errors:
        raise EnvelopeError(
            "; ".join(f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors)
        )


def build_envelope(
    *,
    event_type: str,
    tenant_ref: str,
    data_origin: str,
    payload: dict[str, Any],
    event_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    envelope = {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "tenant_ref": tenant_ref,
        "data_origin": data_origin,
        "occurred_at": (occurred_at or timezone.now()).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "payload": payload,
    }
    validate_envelope(envelope)
    return envelope
