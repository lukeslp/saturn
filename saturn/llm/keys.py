"""API key resolution.

Priority order:
1. `shared.config.ConfigManager` if importable (server/personal use)
2. Environment variables (`ANTHROPIC_API_KEY`, etc.)
3. Fail with `MissingKeyError` naming the env var expected

The fallback path is what `.env.example` documents for users who do not have
the shared config helper on PYTHONPATH.
"""

from __future__ import annotations

import os

_ENV_VAR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "xai": "XAI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "huggingface": "HF_TOKEN",
    "ollama": "OLLAMA_HOST",
}


class MissingKeyError(RuntimeError):
    pass


def _from_config_manager(provider: str) -> str | None:
    # Public-viewer subprocesses set this to bypass the shared key store and
    # only honor explicitly-supplied env vars. See viewer/runner.py.
    if os.environ.get("SATURN_LLM_DISABLE_CONFIG_MANAGER") == "1":
        return None
    try:
        from config import ConfigManager  # type: ignore
    except ImportError:
        return None
    try:
        cm = ConfigManager(app_name="saturn")
        return cm.get_api_key(provider) or None
    except Exception:
        return None


def load_api_keys(providers: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for p in providers:
        key = _from_config_manager(p)
        if not key:
            env_name = _ENV_VAR.get(p, f"{p.upper()}_API_KEY")
            key = os.environ.get(env_name) or None
        # Ollama is special-cased: the provider class accepts api_key="local"
        # as a sentinel meaning "fall back to OLLAMA_HOST or default
        # localhost:11434". So keyless ollama is a valid configuration, not
        # a missing-key error.
        if not key and p == "ollama":
            key = "local"
        if not key:
            expected = _ENV_VAR.get(p, f"{p.upper()}_API_KEY")
            raise MissingKeyError(
                f"no API key for provider {p!r} (set {expected} or add to "
                f"~/documentation/API_KEYS.md)"
            )
        resolved[p] = key
    return resolved
