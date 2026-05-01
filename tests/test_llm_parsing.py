"""Tests for saturn.llm.parsing — tolerant JSON extraction."""

from __future__ import annotations

import pytest

from saturn.llm.parsing import (
    extract_json,
    parse_critique_payload,
    parse_insight_payload,
)


def test_extract_json_bare():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    raw = 'Sure, here is the JSON:\n```json\n{"a": 1}\n```\nHope that helps.'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_unlabeled_fence():
    raw = "```\n{\"b\": 2}\n```"
    assert extract_json(raw) == {"b": 2}


def test_extract_json_raises_on_no_object():
    with pytest.raises(ValueError, match="no JSON object found"):
        extract_json("there is no json here")


def test_parse_insight_payload_validates_required_keys():
    payload = {"narrative": "x", "confidence": "high", "evidence_keys": ["a"]}
    parsed = parse_insight_payload(payload)
    assert parsed["narrative"] == "x"
    assert parsed["confidence"] == "high"
    assert parsed["evidence_keys"] == ["a"]


def test_parse_insight_payload_rejects_bad_confidence():
    with pytest.raises(ValueError, match="confidence"):
        parse_insight_payload(
            {"narrative": "x", "confidence": "certain", "evidence_keys": []}
        )


def test_parse_insight_payload_rejects_missing_keys():
    with pytest.raises(ValueError, match="missing keys"):
        parse_insight_payload({"narrative": "x"})


def test_parse_insight_payload_coerces_evidence_keys_to_strings():
    parsed = parse_insight_payload(
        {"narrative": "x", "confidence": "low", "evidence_keys": [1, "n_unique"]}
    )
    assert parsed["evidence_keys"] == ["1", "n_unique"]


def test_parse_critique_payload():
    payload = {"verdict": "partial", "reason": "missed the null_rate on author_handle"}
    assert parse_critique_payload(payload) == payload


def test_parse_critique_rejects_bad_verdict():
    with pytest.raises(ValueError, match="verdict"):
        parse_critique_payload({"verdict": "maybe", "reason": "x"})


def test_extract_json_balanced_scan_stops_at_first_closed_object():
    """Greedy regex grabs to the last '}'; balanced scan must stop at the first closed span."""
    raw = '{"a": 1, "b": 2} Hope that helps! {note: stray}'
    assert extract_json(raw) == {"a": 1, "b": 2}


def test_extract_json_handles_strings_with_braces():
    """A '}' inside a JSON string must NOT close the object."""
    raw = '{"k": "value with } inside"}  trailing text'
    assert extract_json(raw) == {"k": "value with } inside"}


def test_extract_json_handles_escaped_quotes():
    raw = r'{"k": "she said \"hi\""} stuff'
    assert extract_json(raw) == {"k": 'she said "hi"'}


def test_extract_json_nested_objects():
    raw = '{"a": {"b": {"c": 1}}}  trailing prose'
    assert extract_json(raw) == {"a": {"b": {"c": 1}}}


def test_extract_json_falls_back_to_greedy_when_balanced_invalid():
    """If the balanced span doesn't parse but the greedy span does, use the greedy one."""
    # Constructed: balanced scanner stops at first `}` (truncated object).
    # No real-world response looks like this; this just guards the fallback path.
    raw = '{"a": 1, "b": [{"x": 2}, {"y": 3}]}'
    assert extract_json(raw) == {"a": 1, "b": [{"x": 2}, {"y": 3}]}
