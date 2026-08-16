"""
LLM provider abstraction.

Keeps vendor specifics out of the service layer so the model or the vendor can
change without touching product logic. The OpenRouter implementation reuses
the langchain client the original app already depended on.

Every provider reports token usage, because AI spend is only controllable if
it is measured per operation.
"""
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, List, Optional


class LLMError(RuntimeError):
    """Raised when a completion cannot be produced. Message is user-safe."""


@dataclass
class Message:
    role: str          # "system" | "user"
    content: str


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMProvider(ABC):
    name: str = "abstract"

    @property
    @abstractmethod
    def available(self) -> bool:
        """False when the provider is not configured, so callers can degrade
        with an honest message instead of failing mid-request."""

    @abstractmethod
    def complete(self, messages: List[Message], *,
                 temperature: float = 0.2, max_tokens: int = 1200) -> Completion: ...

    @abstractmethod
    def stream(self, messages: List[Message], *,
               temperature: float = 0.2, max_tokens: int = 1200) -> Iterator[str]: ...


class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    def __init__(self, model: str = "openai/gpt-4.1-mini", api_key: Optional[str] = None):
        self.model = model
        # None means "take it from configuration"; an explicit "" means "there
        # is no key" and must not silently fall back.
        if api_key is None:
            from docintel.config import settings
            api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
        self._api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self._api_key) and len(self._api_key) >= 30

    def _llm(self, temperature: float, max_tokens: int):
        # Imported lazily so the platform starts without langchain installed.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            openai_api_key=self._api_key,
            openai_api_base="https://openrouter.ai/api/v1",
            default_headers={"X-Title": "DocIntel"},
            timeout=90,
            max_retries=2,
        )

    @staticmethod
    def _convert(messages: List[Message]):
        from langchain_core.messages import HumanMessage, SystemMessage

        return [
            SystemMessage(content=m.content) if m.role == "system"
            else HumanMessage(content=m.content)
            for m in messages
        ]

    def complete(self, messages, *, temperature=0.2, max_tokens=1200) -> Completion:
        if not self.available:
            raise LLMError(
                "No AI provider is configured. Set OPENROUTER_API_KEY to enable "
                "AI features."
            )
        try:
            response = self._llm(temperature, max_tokens).invoke(self._convert(messages))
        except Exception as exc:
            raise LLMError(f"The AI provider could not be reached: {exc}") from exc

        usage = getattr(response, "usage_metadata", None) or {}
        return Completion(
            text=str(response.content),
            model=self.model,
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
        )

    def stream(self, messages, *, temperature=0.2, max_tokens=1200) -> Iterator[str]:
        if not self.available:
            raise LLMError(
                "No AI provider is configured. Set OPENROUTER_API_KEY to enable "
                "AI features."
            )
        try:
            for chunk in self._llm(temperature, max_tokens).stream(self._convert(messages)):
                text = str(chunk.content)
                if text:
                    yield text
        except Exception as exc:
            raise LLMError(f"The AI provider could not be reached: {exc}") from exc


class EchoProvider(LLMProvider):
    """Deterministic provider for tests. Never makes a network call."""
    name = "echo"

    def __init__(self, reply: str = "test reply"):
        self.reply = reply
        self.calls: List[List[Message]] = []

    @property
    def available(self) -> bool:
        return True

    def complete(self, messages, *, temperature=0.2, max_tokens=1200) -> Completion:
        self.calls.append(messages)
        return Completion(text=self.reply, model="echo",
                          prompt_tokens=10, completion_tokens=5)

    def stream(self, messages, *, temperature=0.2, max_tokens=1200) -> Iterator[str]:
        self.calls.append(messages)
        yield self.reply


_provider: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = OpenRouterProvider()
    return _provider


def set_provider(provider: Optional[LLMProvider]) -> None:
    """Swap the provider. Used by tests and by future model routing."""
    global _provider
    _provider = provider
