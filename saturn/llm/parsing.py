"""Tolerant JSON extraction plus schema validation for insight payloads.

Models sometimes wrap JSON in markdown fences or prefix with narrative prose.
One tolerant parser here beats ad-hoc regex scattered through the engine.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OBJ = re.compile(r"\{.*\}", re.DOTALL)

_VALID_CONFIDENCE = {"high", "medium", "low"}
_VALID_VERDICT = {"agree", "disagree", "partial"}
_VALID_ROLES = {
    "identifier", "label", "feature", "metadata", "free_text",
    "timestamp", "numeric_target", "foreign_key", "other",
}
_VALID_CHART_KINDS = {"histogram", "bar", "donut", "length"}


def extract_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    fence = _FENCE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    obj_match = _OBJ.search(raw)
    if not obj_match:
        raise ValueError("no JSON object found in response")
    return json.loads(obj_match.group(0))


def parse_insight_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"narrative", "confidence", "evidence_keys"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"insight payload missing keys: {sorted(missing)}")
    if payload["confidence"] not in _VALID_CONFIDENCE:
        raise ValueError(f"invalid confidence: {payload['confidence']!r}")
    if not isinstance(payload["evidence_keys"], list):
        raise ValueError("evidence_keys must be a list")
    out: dict[str, Any] = {
        "narrative": str(payload["narrative"]),
        "confidence": payload["confidence"],
        "evidence_keys": [str(k) for k in payload["evidence_keys"]],
    }
    # v2 optional curation fields. Silently drop anything the model fluffed.
    role = payload.get("role")
    if isinstance(role, str) and role in _VALID_ROLES:
        out["role"] = role
    treatment = payload.get("treatment")
    if isinstance(treatment, str) and treatment.strip():
        out["treatment"] = treatment.strip()
    fc = payload.get("featured_charts")
    if isinstance(fc, list):
        cleaned = []
        for item in fc:
            if not isinstance(item, dict):
                continue
            col = item.get("column")
            kind = item.get("kind")
            if not isinstance(col, str) or not col:
                continue
            if kind not in _VALID_CHART_KINDS:
                continue
            cleaned.append({
                "column": col,
                "kind": kind,
                "caption": str(item.get("caption", "")).strip(),
            })
        if cleaned:
            out["featured_charts"] = cleaned[:5]  # cap at 5 per the prompt
    return out


def parse_critique_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"verdict", "reason"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"critique payload missing keys: {sorted(missing)}")
    if payload["verdict"] not in _VALID_VERDICT:
        raise ValueError(f"invalid verdict: {payload['verdict']!r}")
    return {"verdict": payload["verdict"], "reason": str(payload["reason"])}
