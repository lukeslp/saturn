"""Thin wrapper over ~/shared/llm_providers.ProviderFactory.

Every provider call in saturn goes through this module. No direct vendor SDK
imports are allowed elsewhere in the codebase — this is the single choke point.

`~/shared` must be on `PYTHONPATH`. When the viewer/LLM extras are installed
without shared present, the import error surfaces only when `--llm` is used,
keeping the deterministic pass unaffected.
"""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

from llm_providers import Message, ProviderFactory  # type: ignore


# Single semaphore shared across every call_provider invocation. Default 1
# because the viewer can launch many parallel backfill jobs against the same
# provider account; Anthropic enforces a concurrent-connection cap and will
# 429 the whole batch if we don't serialize. Callers that don't share an
# account (e.g. a dev on their laptop) can raise it via env var.
_CONCURRENCY = max(1, int(os.environ.get("SATURN_LLM_CONCURRENCY", "1")))
_SEMAPHORE = threading.Semaphore(_CONCURRENCY)


ALLOWED_PROVIDERS = {
    "anthropic",
    "openai",
    "groq",
    "gemini",
    "mistral",
    "cohere",
    "xai",
    "perplexity",
    "huggingface",
    "ollama",
}


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    model: str | None = None

    def label(self) -> str:
        return f"{self.provider}:{self.model or 'default'}"


def parse_provider_spec(raw: str) -> ProviderSpec:
    if ":" in raw:
        provider, model = raw.split(":", 1)
    else:
        provider, model = raw, None
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}. allowed: {', '.join(sorted(ALLOWED_PROVIDERS))}"
        )
    return ProviderSpec(provider=provider, model=model)


_MAX_RETRIES = int(os.environ.get("SATURN_LLM_MAX_RETRIES", "3"))


def _is_rate_limit(exc: Exception) -> bool:
    """Detect 429-style rate-limit errors across providers without importing SDKs."""
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
    """Issue one completion. Returns (raw_content, usage_dict).

    Concurrent calls are gated by a module-level semaphore (default 1) because
    Anthropic enforces concurrent-connection caps per account. Rate-limited
    calls retry with exponential backoff up to `SATURN_LLM_MAX_RETRIES` times.
    """
    provider = ProviderFactory.create_provider(
        spec.provider, api_key=api_key, model=spec.model
    )
    messages = [
        Message(role="system", content=system),
        Message(role="user", content=user),
    ]

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        with _SEMAPHORE:
            try:
                response = provider.complete(messages, **provider_kwargs)
                usage = dict(response.usage) if response.usage else {}
                return response.content, usage
            except Exception as e:
                last_exc = e
                if attempt >= _MAX_RETRIES or not _is_rate_limit(e):
                    raise
        # backoff outside the semaphore so other callers can proceed
        sleep = min(32.0, (2 ** attempt) + random.uniform(0, 0.5))
        time.sleep(sleep)

    # unreachable, but keep the type checker happy
    assert last_exc is not None
    raise last_exc
