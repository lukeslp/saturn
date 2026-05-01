"""Tests for the v2 curated insight schema (role/treatment/featured_charts)."""

from __future__ import annotations

import json

import pytest

from saturn.insights import Insight, InsightBundle
from saturn.llm.parsing import parse_insight_payload
from saturn.report import ReportData


# ---------- parse_insight_payload accepts new fields ------------------------


def test_parse_keeps_role_and_treatment_when_valid():
    payload = {
        "narrative": "x", "confidence": "high", "evidence_keys": [],
        "role": "feature", "treatment": "log-transform before regression",
    }
    out = parse_insight_payload(payload)
    assert out["role"] == "feature"
    assert out["treatment"] == "log-transform before regression"


def test_parse_silently_drops_invalid_role():
    payload = {
        "narrative": "x", "confidence": "high", "evidence_keys": [],
        "role": "the_protagonist",  # not in allowlist
    }
    out = parse_insight_payload(payload)
    assert "role" not in out


def test_parse_silently_drops_empty_treatment():
    payload = {
        "narrative": "x", "confidence": "high", "evidence_keys": [],
        "treatment": "   ",
    }
    out = parse_insight_payload(payload)
    assert "treatment" not in out


def test_parse_keeps_featured_charts_when_valid():
    payload = {
        "narrative": "x", "confidence": "high", "evidence_keys": [],
        "featured_charts": [
            {"column": "alt_text", "kind": "length", "caption": "long descriptions dominate"},
            {"column": "source_mode", "kind": "donut", "caption": ""},
        ],
    }
    out = parse_insight_payload(payload)
    assert len(out["featured_charts"]) == 2
    assert out["featured_charts"][0]["column"] == "alt_text"
    assert out["featured_charts"][1]["caption"] == ""


def test_parse_drops_charts_with_unknown_kind():
    payload = {
        "narrative": "x", "confidence": "high", "evidence_keys": [],
        "featured_charts": [
            {"column": "alt_text", "kind": "wordcloud", "caption": ""},  # not a kind we render
            {"column": "src", "kind": "donut", "caption": ""},
        ],
    }
    out = parse_insight_payload(payload)
    assert len(out["featured_charts"]) == 1
    assert out["featured_charts"][0]["column"] == "src"


def test_parse_caps_featured_charts_at_5():
    payload = {
        "narrative": "x", "confidence": "high", "evidence_keys": [],
        "featured_charts": [{"column": f"c{i}", "kind": "histogram", "caption": ""} for i in range(10)],
    }
    out = parse_insight_payload(payload)
    assert len(out["featured_charts"]) == 5


# ---------- Insight dataclass roundtrip --------------------------------------


def test_insight_to_dict_includes_curation_fields_when_set():
    ins = Insight(
        scope="column", target="x", narrative="n", confidence="high",
        evidence_keys=["k"], model="anthropic:opus",
        role="feature", treatment="log-transform",
        featured_charts=[],
    )
    d = ins.to_dict()
    assert d["role"] == "feature"
    assert d["treatment"] == "log-transform"
    # featured_charts empty → omitted
    assert "featured_charts" not in d


def test_insight_to_dict_omits_curation_fields_when_unset():
    ins = Insight(
        scope="column", target="x", narrative="n", confidence="high",
        evidence_keys=[], model="m",
    )
    d = ins.to_dict()
    assert "role" not in d
    assert "treatment" not in d
    assert "featured_charts" not in d


def test_dataset_insight_carries_featured_charts_through_to_dict():
    ins = Insight(
        scope="dataset", target="__global__", narrative="n", confidence="high",
        evidence_keys=[], model="m",
        featured_charts=[{"column": "alt_text", "kind": "length", "caption": "ok"}],
    )
    d = ins.to_dict()
    assert d["featured_charts"][0]["column"] == "alt_text"


# ---------- ReportData.from_findings round-trip ------------------------------


def test_from_findings_restores_role_and_treatment():
    payload = {
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 10, "sampled_rows": 10, "seed": 0,
                 "mode": "full", "generated_at": "2026-05-01T00:00:00+00:00"},
        "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
        "columns": [{"column": "a", "kind": "numeric", "n": 10, "n_null": 0,
                     "n_unique": 10, "stats": {}, "extras": {}, "alerts": [],
                     "null_rate": 0.0}],
        "insights": {
            "providers": ["anthropic:claude-opus-4-7"],
            "insights": [
                {
                    "scope": "column", "target": "a",
                    "narrative": "x", "confidence": "high", "evidence_keys": [],
                    "model": "anthropic:claude-opus-4-7", "critiques": [],
                    "role": "feature", "treatment": "normalize",
                },
                {
                    "scope": "dataset", "target": "__global__",
                    "narrative": "y", "confidence": "high", "evidence_keys": [],
                    "model": "anthropic:claude-opus-4-7", "critiques": [],
                    "featured_charts": [{"column": "a", "kind": "histogram", "caption": "skewed"}],
                },
            ],
            "total_usage": {}, "errors": [],
        },
    }
    data = ReportData.from_findings(payload)
    insights = {i.target: i for i in data.insight_bundle.insights}
    assert insights["a"].role == "feature"
    assert insights["a"].treatment == "normalize"
    assert insights["__global__"].featured_charts[0]["column"] == "a"


# ---------- prompt version bump ---------------------------------------------


def test_prompt_version_bumped_to_v2():
    """Schema changed; PROMPT_VERSION must reflect it so re-runs are reproducible."""
    from saturn.llm.prompts import PROMPT_VERSION

    assert PROMPT_VERSION == "saturn-insight-v2"


# ---------- _featured_columns helper ----------------------------------------


def test_featured_columns_helper_extracts_from_insights(tmp_path):
    from saturn.viewer.app import _featured_columns
    from saturn.viewer.loader import load_findings

    payload = {
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0,
                 "mode": "full", "generated_at": "2026-05-01T00:00:00+00:00"},
        "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
        "columns": [],
        "insights": {
            "providers": ["x"],
            "insights": [
                {"scope": "dataset", "target": "__global__",
                 "narrative": "n", "confidence": "high", "evidence_keys": [],
                 "model": "x", "critiques": [],
                 "featured_charts": [
                     {"column": "alt_text", "kind": "length", "caption": ""},
                     {"column": "source", "kind": "donut", "caption": ""},
                 ]},
            ],
            "total_usage": {}, "errors": [],
        },
    }
    p = tmp_path / "demo.json"
    p.write_text(json.dumps(payload))
    doc = load_findings(p)
    assert _featured_columns(doc) == ["alt_text", "source"]


def test_featured_columns_returns_empty_when_no_insights(tmp_path):
    from saturn.viewer.app import _featured_columns
    from saturn.viewer.loader import load_findings

    payload = {
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0,
                 "mode": "full", "generated_at": "2026-05-01T00:00:00+00:00"},
        "schema": {}, "language_counts": {}, "notes": [], "columns": [],
    }
    p = tmp_path / "demo.json"
    p.write_text(json.dumps(payload))
    doc = load_findings(p)
    assert _featured_columns(doc) == []


# ---------- prompts ask for the new fields ----------------------------------


def test_column_prompt_asks_for_role_and_treatment():
    from saturn.llm.prompts import build_column_prompt

    sys, _user = build_column_prompt({"column": "x", "kind": "numeric", "n": 10,
                                      "null_rate": 0.0, "n_unique": 10,
                                      "stats": {}, "alerts": []})
    assert "role" in sys
    assert "treatment" in sys
    # roles allowlist surfaces in the prompt
    assert "identifier" in sys
    assert "feature" in sys


def test_dataset_prompt_asks_for_featured_charts():
    from saturn.llm.prompts import build_dataset_prompt

    sys, _user = build_dataset_prompt({"source": "s", "row_count": 10,
                                       "column_count": 1, "kinds": {},
                                       "columns": []})
    assert "featured_charts" in sys
    assert "histogram" in sys
    assert "donut" in sys
