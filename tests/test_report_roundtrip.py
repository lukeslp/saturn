"""Round-trip tests for ReportData.to_findings / from_findings."""

from __future__ import annotations

from saturn.insights import Critique, Insight, InsightBundle
from saturn.profilers import Alert, ProfileResult
from saturn.report import ReportData, assemble


def _mk() -> ReportData:
    results = [
        ProfileResult(
            column="alt_text",
            kind="text",
            n=100,
            n_null=5,
            n_unique=80,
            stats={"len_mean": 200.0, "duplicate_rate": 0.1},
            extras={"language_counts": {"en": 90, "es": 5}},
            alerts=[Alert("info", "multilingual", "2 languages")],
        ),
    ]
    data = assemble(
        source="hf://t/d", row_count=100, sampled_rows=100, seed=42,
        schema={"alt_text": "text"}, results=results, mode="full",
    )
    data.insight_bundle = InsightBundle(
        providers=["anthropic:x"],
        insights=[
            Insight(
                scope="column", target="alt_text",
                narrative="The column is mostly English.",
                confidence="high",
                evidence_keys=["language_counts"],
                model="anthropic:x",
                critiques=[Critique(reviewer_model="openai:y", verdict="agree", reason="data supports it.")],
            )
        ],
        total_usage={"input_tokens": 10, "output_tokens": 5},
    )
    return data


def test_roundtrip_preserves_meta_and_schema():
    data = _mk()
    restored = ReportData.from_findings(data.to_findings())
    assert restored.meta.source == data.meta.source
    assert restored.meta.row_count == data.meta.row_count
    assert restored.schema == data.schema


def test_roundtrip_preserves_results_and_alerts():
    data = _mk()
    restored = ReportData.from_findings(data.to_findings())
    assert len(restored.results) == 1
    r = restored.results[0]
    assert r.column == "alt_text"
    assert r.kind == "text"
    assert r.stats["len_mean"] == 200.0
    assert r.extras["language_counts"]["en"] == 90
    assert r.alerts[0].code == "multilingual"
    assert r.null_rate == 0.05


def test_roundtrip_preserves_insight_bundle():
    data = _mk()
    restored = ReportData.from_findings(data.to_findings())
    assert restored.insight_bundle is not None
    bundle = restored.insight_bundle
    assert bundle.providers == ["anthropic:x"]
    assert len(bundle.insights) == 1
    ins = bundle.insights[0]
    assert ins.narrative == "The column is mostly English."
    assert ins.confidence == "high"
    assert len(ins.critiques) == 1
    assert ins.critiques[0].verdict == "agree"


def test_roundtrip_when_no_insight_bundle():
    results = [
        ProfileResult(column="x", kind="numeric", n=1, n_null=0, n_unique=1,
                      stats={}, extras={}, alerts=[])
    ]
    data = assemble(source="s", row_count=1, sampled_rows=1, seed=0,
                    schema={"x": "numeric"}, results=results)
    restored = ReportData.from_findings(data.to_findings())
    assert restored.insight_bundle is None
    assert restored.results[0].column == "x"
