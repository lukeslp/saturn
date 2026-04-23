"""Tests for saturn.llm.gateway — wrapper around ~/shared/llm_providers.ProviderFactory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from saturn.llm.gateway import ProviderSpec, call_provider, parse_provider_spec


def test_parse_provider_spec_defaults_model():
    spec = parse_provider_spec("anthropic")
    assert spec.provider == "anthropic"
    assert spec.model is None


def test_parse_provider_spec_parses_colon():
    spec = parse_provider_spec("openai:gpt-4o-mini")
    assert spec.provider == "openai"
    assert spec.model == "gpt-4o-mini"


def test_parse_provider_spec_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        parse_provider_spec("nonsense")


def test_provider_spec_label():
    assert ProviderSpec("anthropic", "claude").label() == "anthropic:claude"
    assert ProviderSpec("groq", None).label() == "groq:default"


def test_call_provider_wires_messages_and_returns_content_usage():
    fake_response = MagicMock()
    fake_response.content = '{"narrative": "x", "confidence": "high", "evidence_keys": []}'
    fake_response.usage = {"input_tokens": 10, "output_tokens": 5}
    fake_response.model = "claude-sonnet-4-6"

    fake_provider = MagicMock()
    fake_provider.complete.return_value = fake_response

    with patch("saturn.llm.gateway.ProviderFactory") as mock_factory:
        mock_factory.create_provider.return_value = fake_provider
        content, usage = call_provider(
            ProviderSpec(provider="anthropic", model="claude-sonnet-4-6"),
            system="sys",
            user="user",
            api_key="sk-test",
        )

    assert content.startswith("{")
    assert usage == {"input_tokens": 10, "output_tokens": 5}
    mock_factory.create_provider.assert_called_once_with(
        "anthropic", api_key="sk-test", model="claude-sonnet-4-6"
    )
    # Must send a system message + user message, in that order
    messages = fake_provider.complete.call_args[0][0]
    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "sys"
    assert messages[1].content == "user"


def test_call_provider_handles_missing_usage_gracefully():
    fake_response = MagicMock()
    fake_response.content = '{"x": 1}'
    fake_response.usage = None
    fake_provider = MagicMock()
    fake_provider.complete.return_value = fake_response

    with patch("saturn.llm.gateway.ProviderFactory") as mock_factory:
        mock_factory.create_provider.return_value = fake_provider
        _content, usage = call_provider(
            ProviderSpec("anthropic", None),
            system="s",
            user="u",
            api_key="sk",
        )
    assert usage == {}


def test_call_provider_retries_on_rate_limit():
    """429-like errors should trigger exponential backoff + retry."""
    from unittest.mock import MagicMock, patch

    fake_response = MagicMock()
    fake_response.content = "{}"
    fake_response.usage = {"input_tokens": 1}

    class FakeRateLimitError(Exception):
        pass

    calls = {"n": 0}

    def _complete(messages, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeRateLimitError("429 rate_limit_error: concurrent limit")
        return fake_response

    fake_provider = MagicMock()
    fake_provider.complete.side_effect = _complete

    with patch("saturn.llm.gateway.ProviderFactory") as mock_factory, \
         patch("saturn.llm.gateway.time.sleep"):  # skip backoff waits
        mock_factory.create_provider.return_value = fake_provider
        content, _ = call_provider(
            ProviderSpec("anthropic", None),
            system="s", user="u", api_key="sk",
        )
    assert calls["n"] == 3
    assert content == "{}"


def test_call_provider_does_not_retry_non_rate_limit_errors():
    """Non-429 errors should fail fast, not burn retries."""
    from unittest.mock import MagicMock, patch

    fake_provider = MagicMock()
    fake_provider.complete.side_effect = ValueError("bad request")

    with patch("saturn.llm.gateway.ProviderFactory") as mock_factory, \
         patch("saturn.llm.gateway.time.sleep") as mock_sleep:
        mock_factory.create_provider.return_value = fake_provider
        try:
            call_provider(
                ProviderSpec("anthropic", None),
                system="s", user="u", api_key="sk",
            )
            assert False, "should have raised"
        except ValueError:
            pass
    assert fake_provider.complete.call_count == 1
    mock_sleep.assert_not_called()
