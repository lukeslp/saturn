"""Tests for saturn.llm.engine — insight orchestration with fail-open."""

from __future__ import annotations

from unittest.mock import patch

from saturn.insights import InsightBundle
from saturn.llm.engine import run_insights
from saturn.llm.gateway import ProviderSpec
from saturn.profilers import ProfileResult
from saturn.report import assemble


def _mk_report():
    return assemble(
        source="hf://t/d",
        row_count=100,
        sampled_rows=100,
        seed=42,
        schema={"alt_text": "text"},
        results=[
            ProfileResult(
                column="alt_text",
                kind="text",
                n=100,
                n_null=0,
                n_unique=90,
                stats={"len_mean": 200.0},
                extras={},
                alerts=[],
            )
        ],
        mode="full",
    )


def _fake_call(sequence):
    """Build a side_effect that returns each response in order."""
    calls = iter(sequence)

    def _inner(spec, *, system, user, api_key, **kw):
        return next(calls)

    return _inner


def test_run_insights_single_provider_returns_dataset_and_column_insights():
    resp = (
        '{"narrative": "looks fine", "confidence": "high", "evidence_keys": ["n"]}',
        {"input_tokens": 10, "output_tokens": 5},
    )
    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", side_effect=_fake_call([resp, resp])):
        bundle = run_insights(
            report,
            specs=[ProviderSpec("anthropic", "claude-sonnet-4-6")],
            api_keys={"anthropic": "sk-test"},
        )
    assert isinstance(bundle, InsightBundle)
    scopes = [i.scope for i in bundle.insights]
    assert scopes.count("dataset") == 1
    assert scopes.count("column") == 1
    assert bundle.total_usage["input_tokens"] == 20
    assert bundle.errors == []


def test_run_insights_second_provider_critiques_first():
    primary = (
        '{"narrative": "all good", "confidence": "high", "evidence_keys": ["n"]}',
        {"input_tokens": 10, "output_tokens": 5},
    )
    critic = (
        '{"verdict": "disagree", "reason": "mean length of 200 is atypical"}',
        {"input_tokens": 8, "output_tokens": 4},
    )
    sequence = [primary, critic, primary, critic]
    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", side_effect=_fake_call(sequence)):
        bundle = run_insights(
            report,
            specs=[
                ProviderSpec("anthropic", "claude-sonnet-4-6"),
                ProviderSpec("openai", "gpt-4o-mini"),
            ],
            api_keys={"anthropic": "sk-a", "openai": "sk-o"},
        )
    dataset_insight = next(i for i in bundle.insights if i.scope == "dataset")
    assert len(dataset_insight.critiques) == 1
    assert dataset_insight.critiques[0].verdict == "disagree"
    # critic usage merged into total
    assert bundle.total_usage["input_tokens"] == 10 + 8 + 10 + 8


def test_run_insights_fails_open_on_provider_error():
    def _boom(spec, *, system, user, api_key, **kw):
        raise RuntimeError("network down")

    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", side_effect=_boom):
        bundle = run_insights(
            report,
            specs=[ProviderSpec("anthropic", "claude-sonnet-4-6")],
            api_keys={"anthropic": "sk"},
        )
    assert bundle.insights == []
    assert len(bundle.errors) >= 1
    assert "network down" in bundle.errors[0]["message"]


def test_run_insights_fails_open_on_malformed_json():
    bad = ("not even close to json", {})
    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", return_value=bad):
        bundle = run_insights(
            report,
            specs=[ProviderSpec("anthropic")],
            api_keys={"anthropic": "sk"},
        )
    assert bundle.insights == []
    assert bundle.errors  # at least one error recorded


def test_run_insights_requires_specs():
    import pytest
    with pytest.raises(ValueError, match="at least one"):
        run_insights(_mk_report(), specs=[], api_keys={})


def test_run_insights_critic_error_does_not_drop_primary_insight():
    primary = (
        '{"narrative": "x", "confidence": "high", "evidence_keys": []}',
        {"input_tokens": 1, "output_tokens": 1},
    )
    calls = {"n": 0}

    def _call(spec, *, system, user, api_key, **kw):
        calls["n"] += 1
        # every even-indexed call (the critic calls) blows up
        if calls["n"] % 2 == 0:
            raise RuntimeError("critic 500")
        return primary

    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", side_effect=_call):
        bundle = run_insights(
            report,
            specs=[ProviderSpec("anthropic"), ProviderSpec("openai")],
            api_keys={"anthropic": "a", "openai": "o"},
        )
    # Both primary insights survived (dataset + one column)
    assert len(bundle.insights) == 2
    # Critic failures recorded
    assert any("critic 500" in e["message"] for e in bundle.errors)
