"""Deterministic provider for tests and for development without an API key."""

from __future__ import annotations

import hashlib
import json

from .base import LLMMessage, LLMProvider, LLMResult, LLMUsageInfo


class MockProvider(LLMProvider):
    """Returns a fixed or hash-based reply. With ``json_schema`` it returns a JSON object
    with every required property filled: ``canned_json`` values first, then defaults
    (reply text, confidence 0.9, booleans false, lists empty)."""

    name = "mock"

    def __init__(
        self,
        model: str = "mock-model",
        canned_reply: str | None = None,
        canned_json: dict | None = None,
    ):
        self.model = model
        self.canned_reply = canned_reply
        self.canned_json = canned_json
        self.calls: list[dict] = []

    def _reply_text(self, messages: list[LLMMessage]) -> str:
        if self.canned_reply is not None:
            return self.canned_reply
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        digest = hashlib.sha256(last_user.encode()).hexdigest()[:8]
        return f"[mock:{digest}] Cảm ơn bạn đã nhắn. Mình sẽ phản hồi sớm nhé."

    def _json_for(self, schema: dict, reply: str) -> str:
        data: dict = {}
        for key, spec in (schema.get("properties") or {}).items():
            kind = spec.get("type")
            if key in (self.canned_json or {}):
                data[key] = self.canned_json[key]
            elif kind == "string":
                data[key] = reply if key in ("reply", "summary") else ""
            elif kind == "number":
                data[key] = 0.9
            elif kind == "integer":
                data[key] = 5
            elif kind == "boolean":
                data[key] = False
            elif kind == "array":
                data[key] = []
            else:
                data[key] = None
        return json.dumps(data, ensure_ascii=False)

    def generate(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        max_tokens: int = 1024,
        *,
        model: str | None = None,
        cacheable_system: str | None = None,
        json_schema: dict | None = None,
        effort: str | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "max_tokens": max_tokens,
                "model": model,
                "cacheable_system": cacheable_system,
                "json_schema": json_schema,
                "effort": effort,
            }
        )
        reply = self._reply_text(messages)
        text = self._json_for(json_schema, reply) if json_schema else reply[: max_tokens * 4]
        words_in = sum(len(m.content.split()) for m in messages) + len((system or "").split())
        return LLMResult(
            text=text,
            model=model or self.model,
            provider=self.name,
            stop_reason="end_turn",
            usage=LLMUsageInfo(input_tokens=words_in, output_tokens=len(text.split())),
        )
