"""Tests for saturn.insights dataclasses."""

from __future__ import annotations

import json

from saturn.insights import Critique, Insight, InsightBundle


def test_insight_serialises_to_dict():
    ins = Insight(
        scope="column",
        target="alt_text",
        narrative="The column is 98% English with 8.5% duplicates.",
        confidence="high",
        evidence_keys=["null_rate", "language_counts", "duplicate_rate"],
        model="anthropic:claude-sonnet-4-6",
    )
    d = ins.to_dict()
    assert d["scope"] == "column"
    assert d["target"] == "alt_text"
    assert d["evidence_keys"] == ["null_rate", "language_counts", "duplicate_rate"]
    assert d["critiques"] == []


def test_critique_attaches_to_insight():
    ins = Insight(
        scope="dataset",
        target="__global__",
        narrative="looks fine",
        confidence="medium",
        evidence_keys=[],
        model="anthropic:claude-sonnet-4-6",
    )
    crit = Critique(
        reviewer_model="openai:gpt-4o",
        verdict="disagree",
        reason="null rate on author_handle is 100% on firehose, that is not 'fine'.",
    )
    ins.critiques.append(crit)
    d = ins.to_dict()
    assert d["critiques"][0]["verdict"] == "disagree"
    assert d["critiques"][0]["reviewer_model"] == "openai:gpt-4o"


def test_insight_bundle_roundtrips_json():
    bundle = InsightBundle(
        providers=["anthropic:claude-sonnet-4-6", "openai:gpt-4o"],
        insights=[
            Insight(
                scope="dataset",
                target="__global__",
                narrative="x",
                confidence="high",
                evidence_keys=["row_count"],
                model="anthropic:claude-sonnet-4-6",
            )
        ],
        total_usage={"input_tokens": 123, "output_tokens": 45},
    )
    serialised = json.dumps(bundle.to_dict())
    restored = json.loads(serialised)
    assert restored["providers"] == ["anthropic:claude-sonnet-4-6", "openai:gpt-4o"]
    assert restored["total_usage"]["input_tokens"] == 123
    assert restored["insights"][0]["scope"] == "dataset"


def test_insight_bundle_has_empty_errors_by_default():
    bundle = InsightBundle(providers=[])
    assert bundle.errors == []
    assert bundle.insights == []
    assert bundle.total_usage == {}
