"""Tests for saturn.llm.compare_evidence."""

from __future__ import annotations

from saturn.compare import ColumnComparison, CompareReport, CompareSide
from saturn.llm.compare_evidence import (
    compare_column_evidence,
    compare_dataset_evidence,
)
from saturn.profilers import Alert, ProfileResult


def _col(name: str, n: int, null: int, lm: float, alerts=None):
    return ProfileResult(
        column=name,
        kind="text",
        n=n,
        n_null=null,
        n_unique=n - null,
        stats={"len_mean": lm, "duplicate_rate": 0.1},
        extras={"language_counts": {"en": n - null, "__engine": "x"}},
        alerts=alerts or [],
    )


def _cc(name: str, a: ProfileResult, b: ProfileResult, delta: dict):
    return ColumnComparison(column=name, kind=a.kind, a=a, b=b, delta=delta)


def _report() -> CompareReport:
    a = _col("alt_text", n=1000, null=0, lm=200.0)
    b = _col("alt_text", n=500, null=100, lm=281.0, alerts=[Alert("info", "drift", "x")])
    columns = [
        _cc(
            "alt_text",
            a,
            b,
            delta={
                "len_mean_delta": 81.0,
                "len_mean_a": 200.0,
                "len_mean_b": 281.0,
                "null_rate_delta": 0.2,
                "top_value_jaccard": 0.45,
                "language_jaccard": 0.35,
            },
        ),
    ]
    return CompareReport(
        a=CompareSide(label="curated", source="a", row_count=1000, schema={}),
        b=CompareSide(label="firehose", source="b", row_count=500, schema={}),
        columns=columns,
        generated_at="2026-04-22T00:00:00+00:00",
    )


def test_compare_column_evidence_projects_both_sides_and_delta():
    report = _report()
    ev = compare_column_evidence(report.columns[0], a_label="curated", b_label="firehose")
    assert ev["column"] == "alt_text"
    assert ev["kind"] == "text"
    assert ev["kind_a"] == "text"
    assert ev["kind_b"] == "text"
    assert ev["compatible"] is True
    assert ev["a"]["label"] == "curated"
    assert ev["a"]["stats"]["len_mean"] == 200.0
    assert ev["b"]["label"] == "firehose"
    assert ev["b"]["null_rate"] == 0.2
    assert ev["delta"]["len_mean_delta"] == 81.0
    assert ev["delta"]["top_value_jaccard"] == 0.45
    # __engine stripped
    assert "__engine" not in ev["a"]["language_counts"]


def test_compare_column_evidence_exposes_schema_drift():
    numeric = ProfileResult(
        column="value", kind="numeric", n=2, n_null=0, n_unique=2,
        stats={"mean": 1.5}, extras={}, alerts=[],
    )
    text = ProfileResult(
        column="value", kind="text", n=2, n_null=0, n_unique=2,
        stats={"len_mean": 3.5}, extras={}, alerts=[],
    )
    cc = ColumnComparison(
        column="value", kind="numeric", a=numeric, b=text, delta={},
        notes=["schema drift"], kind_a="numeric", kind_b="text", compatible=False,
    )

    ev = compare_column_evidence(cc, a_label="A", b_label="B")

    assert ev["kind_a"] == "numeric"
    assert ev["kind_b"] == "text"
    assert ev["compatible"] is False


def test_compare_evidence_honors_redaction_env(monkeypatch):
    # categorical top_value is a literal cell value; the env switch must scrub it
    cat = ProfileResult(
        column="author", kind="categorical", n=100, n_null=0, n_unique=3,
        stats={"top_value": "alice@example.com", "entropy": 1.0}, extras={}, alerts=[],
    )
    cc = ColumnComparison(column="author", kind="categorical", a=cat, b=cat, delta={})

    # default: literal value present
    ev_open = compare_column_evidence(cc, a_label="A", b_label="B")
    assert ev_open["a"]["stats"]["top_value"] == "alice@example.com"

    monkeypatch.setenv("SATURN_REDACT_EVIDENCE_VALUES", "1")
    ev_redacted = compare_column_evidence(cc, a_label="A", b_label="B")
    assert "top_value" not in ev_redacted["a"]["stats"]
    assert ev_redacted["a"]["stats"]["entropy"] == 1.0


def test_compare_evidence_redacts_literal_delta_fields(monkeypatch):
    cat_a = ProfileResult(
        column="author", kind="categorical", n=100, n_null=0, n_unique=3,
        stats={"top_value": "alice@example.com", "entropy": 1.0}, extras={}, alerts=[],
    )
    cat_b = ProfileResult(
        column="author", kind="categorical", n=100, n_null=0, n_unique=3,
        stats={"top_value": "bob@example.com", "entropy": 1.2}, extras={}, alerts=[],
    )
    cc = ColumnComparison(
        column="author",
        kind="categorical",
        a=cat_a,
        b=cat_b,
        delta={
            "top_value_a": "alice@example.com",
            "top_value_b": "bob@example.com",
            "entropy_delta": 0.2,
        },
    )

    monkeypatch.setenv("SATURN_REDACT_EVIDENCE_VALUES", "1")
    ev = compare_column_evidence(cc, a_label="A", b_label="B")

    assert "top_value_a" not in ev["delta"]
    assert "top_value_b" not in ev["delta"]
    assert ev["delta"]["entropy_delta"] == 0.2


def test_compare_column_evidence_handles_missing_side():
    # Column only in A
    a = _col("only_a", n=10, null=0, lm=5.0)
    cc = ColumnComparison(column="only_a", kind="text", a=a, b=None, delta={}, notes=["missing in B"])
    ev = compare_column_evidence(cc, a_label="A", b_label="B")
    assert ev["a"] is not None
    assert ev["b"] is None
    assert "missing in B" in ev["notes"]


def test_compare_dataset_evidence_summarises_divergences():
    report = _report()
    ev = compare_dataset_evidence(report)
    assert ev["a_label"] == "curated"
    assert ev["b_label"] == "firehose"
    assert ev["a_row_count"] == 1000
    assert ev["b_row_count"] == 500
    assert "divergences" in ev
    assert ev["divergences"][0]["column"] == "alt_text"
    # Top-level keys stable
    assert set(ev.keys()) == {
        "a_label", "b_label", "a_row_count", "b_row_count",
        "column_count", "divergences",
    }
