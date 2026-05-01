"""Tests for saturn.llm.prompts — deterministic prompt builders."""

from __future__ import annotations

from saturn.llm.prompts import (
    PROMPT_VERSION,
    build_column_prompt,
    build_critique_prompt,
    build_dataset_prompt,
)


def test_column_prompt_is_deterministic_and_cites_evidence_keys():
    ev = {
        "column": "alt_text",
        "kind": "text",
        "n": 1000,
        "null_rate": 0.05,
        "n_unique": 950,
        "stats": {"len_mean": 201.3, "duplicate_rate": 0.085},
        "alerts": ["multilingual"],
        "language_counts": {"en": 900, "es": 50},
    }
    sys1, user1 = build_column_prompt(ev)
    sys2, user2 = build_column_prompt(ev)
    assert sys1 == sys2
    assert user1 == user2
    # System must describe the contract
    assert "evidence_keys" in sys1
    assert "confidence" in sys1
    # User payload must include the column name + at least one stat
    assert "alt_text" in user1
    assert "len_mean" in user1
    # Prompt version tag is stamped on the system prompt for reproducibility
    assert PROMPT_VERSION in sys1


def test_dataset_prompt_includes_version_tag_and_featured_charts():
    ev = {
        "source": "hf://x/y",
        "row_count": 10,
        "column_count": 2,
        "kinds": {"a": "text", "b": "numeric"},
        "columns": [],
    }
    sys, user = build_dataset_prompt(ev)
    assert PROMPT_VERSION in sys
    # `featured_charts` replaced the old `hotspots` field in v2 — single
    # source of "what's worth looking at" rather than two parallel arrays.
    assert "featured_charts" in sys
    assert "hf://x/y" in user


def test_critique_prompt_references_target_insight_and_verdict_set():
    peer = {
        "scope": "column",
        "target": "alt_text",
        "narrative": "This column is mostly English with some duplicates.",
        "confidence": "high",
        "evidence_keys": ["null_rate", "language_counts"],
        "model": "anthropic:claude-sonnet-4-6",
    }
    ev = {
        "column": "alt_text",
        "alerts": ["multilingual"],
        "language_counts": {"en": 900, "es": 50, "fr": 50},
    }
    sys, user = build_critique_prompt(peer_insight=peer, evidence=ev)
    # Model name identifies the peer
    assert "claude-sonnet-4-6" in user
    assert "alt_text" in user
    # Critic must be told the allowed verdicts
    for verdict in ("agree", "disagree", "partial"):
        assert verdict in sys


def test_prompts_produce_valid_json_in_user_payload():
    import json

    ev = {"column": "a", "kind": "numeric", "n": 10, "null_rate": 0.0,
          "n_unique": 10, "stats": {}, "alerts": []}
    _sys, user = build_column_prompt(ev)
    # Strip the leading "Column evidence:\n" prefix and confirm the rest parses
    payload = user.split("\n", 1)[1]
    json.loads(payload)


def test_compare_column_prompt_uses_pair_evidence():
    from saturn.llm.prompts import build_compare_column_prompt, PROMPT_VERSION

    ev = {
        "column": "alt_text",
        "kind": "text",
        "a": {"label": "curated", "n": 1000, "stats": {"len_mean": 200}},
        "b": {"label": "firehose", "n": 500, "stats": {"len_mean": 281}},
        "delta": {"len_mean_delta": 81.0},
    }
    sys, user = build_compare_column_prompt(ev)
    assert PROMPT_VERSION in sys
    assert "curated" in user
    assert "firehose" in user
    assert "81" in user


def test_compare_dataset_prompt_has_hotspots_contract():
    from saturn.llm.prompts import build_compare_dataset_prompt, PROMPT_VERSION

    ev = {
        "a_label": "curated", "b_label": "firehose",
        "a_row_count": 1000, "b_row_count": 500,
        "column_count": 5,
        "divergences": [{"column": "alt_text", "kind": "text", "score": 1.2, "signals": ["len_mean +81"]}],
    }
    sys, user = build_compare_dataset_prompt(ev)
    assert "hotspots" in sys
    assert PROMPT_VERSION in sys
    assert "alt_text" in user
