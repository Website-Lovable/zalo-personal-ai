"""LLM abstraction (Kickoff 4). The app never imports a vendor SDK outside this package."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMUsageInfo:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    provider: str
    stop_reason: str = ""
    usage: LLMUsageInfo = field(default_factory=LLMUsageInfo)

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class LLMError(Exception):
    pass


class LLMProvider(ABC):
    name: str = ""

    @abstractmethod
    def generate(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        max_tokens: int = 1024,
        *,
        model: str | None = None,
        cacheable_system: str | None = None,
    ) -> LLMResult:
        """Generate a reply.

        ``cacheable_system`` is the stable part of the system prompt (owner profile, style);
        providers that support prompt caching cache it, ``system`` holds the volatile part.
        """
