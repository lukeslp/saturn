"""Tests for saturn.llm.evidence — ReportData -> model-ready dict."""

from __future__ import annotations

from saturn.llm.evidence import column_evidence, dataset_evidence
from saturn.profilers import Alert, ProfileResult
from saturn.report import ReportData, assemble


def _mk_report() -> ReportData:
    results = [
        ProfileResult(
            column="alt_text",
            kind="text",
            n=1000,
            n_null=50,
            n_unique=950,
            stats={"len_mean": 201.3, "len_p95": 460, "duplicate_rate": 0.085},
            extras={
                "language_counts": {"en": 900, "es": 50, "__engine": "fasttext:1,000"},
                "top_words": [("the", 500), ("and", 300)],
            },
            alerts=[Alert("info", "multilingual", "2+ languages")],
        ),
        ProfileResult(
            column="cursor",
            kind="text",
            n=1000,
            n_null=0,
            n_unique=800,
            stats={"duplicate_rate": 0.2},
            extras={},
            alerts=[Alert("warn", "near_unique", "most values distinct")],
        ),
    ]
    return assemble(
        source="hf://test/demo",
        row_count=1000,
        sampled_rows=1000,
        seed=42,
        schema={"alt_text": "text", "cursor": "text"},
        results=results,
        mode="full",
    )


def test_column_evidence_extracts_pruned_subset():
    report = _mk_report()
    ev = column_evidence(report, "alt_text")
    assert ev["column"] == "alt_text"
    assert ev["kind"] == "text"
    assert ev["null_rate"] == 0.05
    assert ev["stats"]["len_mean"] == 201.3
    # alerts flattened to code strings only (no message prose, keeps tokens down)
    assert ev["alerts"] == ["multilingual"]
    # provenance key __engine is stripped — implementation detail, not for the model
    assert ev["language_counts"] == {"en": 900, "es": 50}
    assert "__engine" not in ev["language_counts"]
    # top_words capped to first 10
    assert ev["top_words"] == [("the", 500), ("and", 300)]


def test_column_evidence_handles_missing_extras():
    report = _mk_report()
    ev = column_evidence(report, "cursor")
    assert "language_counts" not in ev
    assert "top_words" not in ev
    assert ev["alerts"] == ["near_unique"]


def test_column_evidence_raises_on_unknown_column():
    import pytest
    report = _mk_report()
    with pytest.raises(KeyError, match="nonesuch"):
        column_evidence(report, "nonesuch")


def test_dataset_evidence_summarises_whole_report():
    report = _mk_report()
    ev = dataset_evidence(report)
    assert ev["source"] == "hf://test/demo"
    assert ev["row_count"] == 1000
    assert ev["column_count"] == 2
    assert len(ev["columns"]) == 2
    assert set(ev.keys()) == {"source", "row_count", "column_count", "kinds", "columns"}
    # interestingness ordering — alerts first, then null rate
    assert ev["columns"][0]["column"] in {"alt_text", "cursor"}


def test_column_evidence_redacts_literal_values():
    report = _mk_report()
    ev = column_evidence(report, "alt_text", redact_values=True)
    # aggregates survive, literal user strings are withheld
    assert ev["language_counts"] == {"en": 900, "es": 50}
    assert ev["stats"]["len_mean"] == 201.3
    assert "top_words" not in ev
    assert "top_values" not in ev


def test_dataset_evidence_redact_flag_propagates():
    report = _mk_report()
    ev = dataset_evidence(report, redact_values=True)
    for col in ev["columns"]:
        assert "top_values" not in col
        assert "top_words" not in col


def test_top_values_byte_capped():
    long_val = "x" * 5000
    results = [
        ProfileResult(
            column="blob",
            kind="categorical",
            n=10,
            n_null=0,
            n_unique=2,
            stats={},
            extras={"top_values": [(long_val, 7), ("short", 3)]},
            alerts=[],
        ),
    ]
    report = assemble(
        source="s", row_count=10, sampled_rows=10, seed=0,
        schema={"blob": "categorical"}, results=results, mode="full",
    )
    ev = column_evidence(report, "blob")
    capped_label = ev["top_values"][0][0]
    # 200 content bytes + the 3-byte UTF-8 ellipsis marker
    assert len(capped_label.encode("utf-8")) <= 203
    assert capped_label.endswith("…")
    # short values pass through untouched, preserving the tuple shape
    assert ev["top_values"][1] == ("short", 3)


def test_short_top_words_unchanged_after_byte_cap():
    # regression guard: low-cardinality columns keep their exact prior shape
    report = _mk_report()
    ev = column_evidence(report, "alt_text")
    assert ev["top_words"] == [("the", 500), ("and", 300)]


def test_redaction_withholds_categorical_top_value():
    # regression: stats["top_value"] is a literal cell value and must not leak
    results = [
        ProfileResult(
            column="author",
            kind="categorical",
            n=100,
            n_null=0,
            n_unique=5,
            stats={"top_value": "alice@example.com", "entropy": 1.2},
            extras={"top_values": [("alice@example.com", 60), ("bob", 40)]},
            alerts=[],
        ),
    ]
    report = assemble(
        source="s", row_count=100, sampled_rows=100, seed=0,
        schema={"author": "categorical"}, results=results, mode="full",
    )
    ev = column_evidence(report, "author", redact_values=True)
    assert "top_value" not in ev["stats"]  # literal value withheld
    assert ev["stats"]["entropy"] == 1.2   # aggregate survives
    assert "top_values" not in ev
    # without redaction the value is present (and byte-capped, but short here)
    ev_open = column_evidence(report, "author")
    assert ev_open["stats"]["top_value"] == "alice@example.com"


def test_long_categorical_top_value_byte_capped():
    big = "z" * 4000
    results = [
        ProfileResult(
            column="blob", kind="categorical", n=10, n_null=0, n_unique=1,
            stats={"top_value": big}, extras={}, alerts=[],
        ),
    ]
    report = assemble(
        source="s", row_count=10, sampled_rows=10, seed=0,
        schema={"blob": "categorical"}, results=results, mode="full",
    )
    ev = column_evidence(report, "blob")
    assert len(ev["stats"]["top_value"].encode("utf-8")) <= 203


def test_dataset_evidence_ranks_by_interestingness():
    # Two columns, same alert count, one has higher null rate → null wins
    results = [
        ProfileResult(column="quiet", kind="numeric", n=100, n_null=0, n_unique=100,
                      stats={}, extras={}, alerts=[]),
        ProfileResult(column="leaky", kind="numeric", n=100, n_null=40, n_unique=60,
                      stats={}, extras={}, alerts=[]),
    ]
    report = assemble(source="s", row_count=100, sampled_rows=100, seed=0,
                      schema={"quiet": "numeric", "leaky": "numeric"},
                      results=results, mode="full")
    ev = dataset_evidence(report)
    assert ev["columns"][0]["column"] == "leaky"
