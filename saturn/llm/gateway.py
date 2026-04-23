"""Thin wrapper over ~/shared/llm_providers.ProviderFactory.

Every provider call in saturn goes through this module. No direct vendor SDK
imports are allowed elsewhere in the codebase — this is the single choke point.

`~/shared` must be on `PYTHONPATH`. When the viewer/LLM extras are installed
without shared present, the import error surfaces only when `--llm` is used,
keeping the deterministic pass unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_providers import Message, ProviderFactory  # type: ignore


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


def call_provider(
    spec: ProviderSpec,
    *,
    system: str,
    user: str,
    api_key: str,
    **provider_kwargs: Any,
) -> tuple[str, dict[str, int]]:
    """Issue one completion. Returns (raw_content, usage_dict)."""
    provider = ProviderFactory.create_provider(
        spec.provider, api_key=api_key, model=spec.model
    )
    messages = [
        Message(role="system", content=system),
        Message(role="user", content=user),
    ]
    response = provider.complete(messages, **provider_kwargs)
    usage = dict(response.usage) if response.usage else {}
    return response.content, usage
