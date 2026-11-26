"""Deterministic provider for tests and for development without an API key."""

from __future__ import annotations

import hashlib

from .base import LLMMessage, LLMProvider, LLMResult, LLMUsageInfo


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "mock-model", canned_reply: str | None = None):
        self.model = model
        self.canned_reply = canned_reply
        self.calls: list[dict] = []

    def generate(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        max_tokens: int = 1024,
        *,
        model: str | None = None,
        cacheable_system: str | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "max_tokens": max_tokens,
                "model": model,
                "cacheable_system": cacheable_system,
            }
        )
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if self.canned_reply is not None:
            text = self.canned_reply
        else:
            digest = hashlib.sha256(last_user.encode()).hexdigest()[:8]
            text = f"[mock:{digest}] Cảm ơn bạn đã nhắn. Mình sẽ phản hồi sớm nhé."
        words_in = sum(len(m.content.split()) for m in messages) + len((system or "").split())
        return LLMResult(
            text=text[: max_tokens * 4],
            model=model or self.model,
            provider=self.name,
            stop_reason="end_turn",
            usage=LLMUsageInfo(input_tokens=words_in, output_tokens=len(text.split())),
        )
