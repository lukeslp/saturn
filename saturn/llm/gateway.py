"""Package-owned boundary over LiteLLM's public provider interface."""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    from litellm import completion as _completion
except ImportError:  # pragma: no cover - installs without [llm]
    def _completion(**_: Any) -> Any:
        raise RuntimeError("LLM support requires: pip install 'saturn-dissect[llm]'")


_CONCURRENCY = max(1, int(os.environ.get("SATURN_LLM_CONCURRENCY", "1")))
_SEMAPHORE = threading.Semaphore(_CONCURRENCY)

ALLOWED_PROVIDERS = {
    "anthropic", "openai", "groq", "gemini", "mistral", "cohere",
    "xai", "perplexity", "huggingface", "ollama",
}

_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.0-flash",
    "mistral": "mistral-small-latest",
    "cohere": "command-r-plus",
    "xai": "grok-3-mini",
    "perplexity": "sonar",
    "huggingface": "meta-llama/Llama-3.1-8B-Instruct",
    "ollama": "llama3.2",
}


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    model: str | None = None

    def label(self) -> str:
        return f"{self.provider}:{self.model or 'default'}"


def parse_provider_spec(raw: str) -> ProviderSpec:
    provider, separator, model = raw.partition(":")
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}. allowed: {', '.join(sorted(ALLOWED_PROVIDERS))}"
        )
    return ProviderSpec(provider=provider, model=model if separator else None)


def _model_name(spec: ProviderSpec) -> str:
    model = spec.model or _DEFAULT_MODELS[spec.provider]
    return f"{spec.provider}/{model}"


_MAX_RETRIES = int(os.environ.get("SATURN_LLM_MAX_RETRIES", "3"))


def _is_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "toomany" in name:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def call_provider(
    spec: ProviderSpec,
    *,
    system: str,
    user: str,
    api_key: str,
    **provider_kwargs: Any,
) -> tuple[str, dict[str, int]]:
    """Issue one completion and return its text and token usage."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        with _SEMAPHORE:
            try:
                response = _completion(
                    model=_model_name(spec), messages=messages, api_key=api_key,
                    **provider_kwargs,
                )
                usage_obj = getattr(response, "usage", None)
                if hasattr(usage_obj, "model_dump"):
                    usage = usage_obj.model_dump(exclude_none=True)
                else:
                    usage = dict(usage_obj) if usage_obj else {}
                content = getattr(response, "content", None)
                if content is None:
                    content = response.choices[0].message.content
                return content, usage
            except Exception as exc:
                last_exc = exc
                if attempt >= _MAX_RETRIES or not _is_rate_limit(exc):
                    raise
        time.sleep(min(32.0, (2 ** attempt) + random.uniform(0, 0.5)))

    assert last_exc is not None
    raise last_exc
