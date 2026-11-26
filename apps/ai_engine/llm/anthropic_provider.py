"""Anthropic Claude provider. Phase 1: importable and unit-tested with a mocked transport.

The real key is only needed from Phase 3 (ANTHROPIC_API_KEY in .env).
"""

from __future__ import annotations

from typing import Any

import anthropic

from .base import LLMError, LLMMessage, LLMProvider, LLMResult, LLMUsageInfo


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None,
        default_model: str,
        *,
        client: Any = None,
        http_client: Any = None,
        max_retries: int = 2,
        timeout: float = 60.0,
    ):
        if client is None:
            if not api_key:
                raise LLMError("ANTHROPIC_API_KEY is not set (use LLM_PROVIDER=mock until Phase 3)")
            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "max_retries": max_retries,
                "timeout": timeout,
            }
            if http_client is not None:
                kwargs["http_client"] = http_client
            client = anthropic.Anthropic(**kwargs)
        self.client = client
        self.default_model = default_model

    @staticmethod
    def _system_blocks(system: str | None, cacheable_system: str | None) -> list[dict]:
        blocks: list[dict] = []
        if cacheable_system:
            # Stable prefix (profile/style) first so it can be served from the prompt cache.
            blocks.append(
                {"type": "text", "text": cacheable_system, "cache_control": {"type": "ephemeral"}}
            )
        if system:
            blocks.append({"type": "text", "text": system})
        return blocks

    def generate(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        max_tokens: int = 1024,
        *,
        model: str | None = None,
        cacheable_system: str | None = None,
    ) -> LLMResult:
        params: dict[str, Any] = {
            "model": model or self.default_model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        blocks = self._system_blocks(system, cacheable_system)
        if blocks:
            params["system"] = blocks
        try:
            response = self.client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Anthropic connection error") from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return LLMResult(
            text=text,
            model=response.model,
            provider=self.name,
            stop_reason=response.stop_reason or "",
            usage=LLMUsageInfo(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
        )
