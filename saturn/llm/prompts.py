"""Prompt builders for the insight pass.

Deterministic: the same evidence dict always produces the same (system, user)
tuple. Every system prompt carries a `PROMPT_VERSION` tag so model output is
reproducible by version.
"""

from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "saturn-insight-v1"

_SYSTEM_COLUMN = f"""You are analysing a single column from a dataset profile emitted by saturn, a deterministic dataset dissector.
Return a single JSON object with these keys: narrative (string, 2-4 sentences, plain prose), confidence ("high"|"medium"|"low"), evidence_keys (array of stat keys you used).
Rules:
- Only cite numbers that appear verbatim in the evidence payload.
- Never speculate beyond the evidence. If a signal is missing, say so.
- Flag any signal that would surprise an analyst (severe skew, drift, duplicates, language mix).
- No filler prose. No apologies. No disclaimers.
Tag: {PROMPT_VERSION}
"""

_SYSTEM_DATASET = f"""You are summarising the top-level shape of a dataset from saturn's findings.
Return a single JSON object with these keys: narrative (string, 3-6 sentences), confidence ("high"|"medium"|"low"), evidence_keys (array of stat keys you used), hotspots (array of column names worth follow-up).
Rules:
- Do not list every column. Pick the 3-5 that would change a downstream decision.
- Never invent numbers. Only cite values present in the evidence payload.
Tag: {PROMPT_VERSION}
"""

_SYSTEM_CRITIC = f"""You are the critic. Review a peer model's insight about a saturn finding.
Return a single JSON object with these keys: verdict ("agree"|"disagree"|"partial"), reason (one sentence citing the specific stat that supports your verdict).
Rules:
- Only cite numbers present in the evidence payload.
- "agree" means the peer's narrative is accurate AND complete given the evidence.
- "partial" means partially accurate but missing something material.
- "disagree" means factually wrong.
- One sentence. No hedging.
Tag: {PROMPT_VERSION}
"""


def _as_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, indent=2)


def build_column_prompt(evidence: dict[str, Any]) -> tuple[str, str]:
    user = f"Column evidence:\n{_as_json(evidence)}"
    return _SYSTEM_COLUMN, user


def build_dataset_prompt(evidence: dict[str, Any]) -> tuple[str, str]:
    user = f"Dataset evidence:\n{_as_json(evidence)}"
    return _SYSTEM_DATASET, user


def build_critique_prompt(
    peer_insight: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[str, str]:
    user = (
        f"Peer model: {peer_insight['model']}\n"
        f"Peer insight:\n{_as_json(peer_insight)}\n\n"
        f"Original evidence:\n{_as_json(evidence)}"
    )
    return _SYSTEM_CRITIC, user
