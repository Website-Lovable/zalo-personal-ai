from __future__ import annotations

from django.conf import settings

from .base import LLMError, LLMMessage, LLMProvider, LLMResult, LLMUsageInfo
from .mock_provider import MockProvider

__all__ = [
    "LLMError",
    "LLMMessage",
    "LLMProvider",
    "LLMResult",
    "LLMUsageInfo",
    "MockProvider",
    "get_llm_provider",
    "record_usage",
]


def get_llm_provider(kind: str = "main") -> LLMProvider:
    """Return the configured provider. ``kind`` = main (replies) | fast (classify/summarize)."""
    model = settings.LLM_MODEL_FAST if kind == "fast" else settings.LLM_MODEL_MAIN
    if settings.LLM_PROVIDER == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings.ANTHROPIC_API_KEY, default_model=model)
    if settings.LLM_PROVIDER == "mock":
        return MockProvider(model=f"mock-{model}")
    raise LLMError(f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER}")


def record_usage(tenant, result: LLMResult, purpose: str = ""):
    from apps.ai_engine.models import LLMUsage

    return LLMUsage.objects.create(
        tenant=tenant,
        provider=result.provider,
        model=result.model,
        purpose=purpose,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cache_read_input_tokens=result.usage.cache_read_input_tokens,
        cache_creation_input_tokens=result.usage.cache_creation_input_tokens,
    )
