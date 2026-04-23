"""Tests for saturn.llm.engine.run_compare_insights."""

from __future__ import annotations

from unittest.mock import patch

from saturn.compare import ColumnComparison, CompareReport, CompareSide
from saturn.insights import InsightBundle
from saturn.llm.engine import run_compare_insights
from saturn.llm.gateway import ProviderSpec
from saturn.profilers import ProfileResult


def _pr(col: str, n: int, null: int, lm: float) -> ProfileResult:
    return ProfileResult(
        column=col,
        kind="text",
        n=n,
        n_null=null,
        n_unique=n - null,
        stats={"len_mean": lm, "duplicate_rate": 0.1},
        extras={"language_counts": {"en": n - null}},
        alerts=[],
    )


def _report() -> CompareReport:
    a = _pr("alt_text", 1000, 0, 200.0)
    b = _pr("alt_text", 500, 100, 281.0)
    cc = ColumnComparison(
        column="alt_text",
        kind="text",
        a=a,
        b=b,
        delta={
            "len_mean_delta": 81.0,
            "len_mean_a": 200.0,
            "len_mean_b": 281.0,
            "null_rate_delta": 0.2,
            "language_jaccard": 0.35,
        },
    )
    return CompareReport(
        a=CompareSide(label="curated", source="a", row_count=1000, schema={}),
        b=CompareSide(label="firehose", source="b", row_count=500, schema={}),
        columns=[cc],
        generated_at="2026-04-22T00:00:00+00:00",
    )


def _fake_call(sequence):
    calls = iter(sequence)

    def _inner(spec, *, system, user, api_key, **kw):
        return next(calls)

    return _inner


def test_run_compare_insights_emits_dataset_and_column_scopes():
    resp = (
        '{"narrative": "curated is shorter than firehose", "confidence": "high", "evidence_keys": ["len_mean_delta"]}',
        {"input_tokens": 10, "output_tokens": 5},
    )
    report = _report()
    with patch(
        "saturn.llm.engine.call_provider", side_effect=_fake_call([resp, resp])
    ):
        bundle = run_compare_insights(
            report,
            specs=[ProviderSpec("anthropic", "claude-sonnet-4-6")],
            api_keys={"anthropic": "sk"},
        )
    assert isinstance(bundle, InsightBundle)
    scopes = [i.scope for i in bundle.insights]
    # Both dataset-level and column-level insights use scope="compare"
    assert scopes.count("compare") == 2
    targets = {i.target for i in bundle.insights}
    assert "__global__" in targets
    assert "alt_text" in targets


def test_run_compare_insights_critic_path():
    primary = (
        '{"narrative": "x", "confidence": "high", "evidence_keys": []}',
        {"input_tokens": 1, "output_tokens": 1},
    )
    critic_resp = (
        '{"verdict": "partial", "reason": "missed top_value_jaccard"}',
        {"input_tokens": 1, "output_tokens": 1},
    )
    seq = [primary, critic_resp, primary, critic_resp]
    report = _report()
    with patch("saturn.llm.engine.call_provider", side_effect=_fake_call(seq)):
        bundle = run_compare_insights(
            report,
            specs=[ProviderSpec("anthropic"), ProviderSpec("openai")],
            api_keys={"anthropic": "a", "openai": "b"},
        )
    for ins in bundle.insights:
        assert len(ins.critiques) == 1
        assert ins.critiques[0].verdict == "partial"


def test_run_compare_insights_skips_columns_missing_on_one_side():
    report = _report()
    # Add a column only on A
    report.columns.append(
        ColumnComparison(
            column="only_a",
            kind="text",
            a=_pr("only_a", 10, 0, 5.0),
            b=None,
            delta={},
            notes=["missing in firehose"],
        )
    )
    resp = (
        '{"narrative": "n", "confidence": "high", "evidence_keys": []}',
        {"input_tokens": 0, "output_tokens": 0},
    )
    # Two calls expected: dataset + alt_text. only_a is skipped.
    with patch(
        "saturn.llm.engine.call_provider",
        side_effect=_fake_call([resp, resp]),
    ) as mock_call:
        bundle = run_compare_insights(
            report,
            specs=[ProviderSpec("anthropic")],
            api_keys={"anthropic": "sk"},
        )
    assert mock_call.call_count == 2
    assert {i.target for i in bundle.insights} == {"__global__", "alt_text"}


def test_run_compare_insights_fails_open():
    report = _report()

    def _boom(spec, *, system, user, api_key, **kw):
        raise RuntimeError("net down")

    with patch("saturn.llm.engine.call_provider", side_effect=_boom):
        bundle = run_compare_insights(
            report,
            specs=[ProviderSpec("anthropic")],
            api_keys={"anthropic": "sk"},
        )
    assert bundle.insights == []
    assert len(bundle.errors) >= 1
    assert any("net down" in e["message"] for e in bundle.errors)
