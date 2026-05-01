"""Prompt builders for the insight pass.

Deterministic: the same evidence dict always produces the same (system, user)
tuple. Every system prompt carries a `PROMPT_VERSION` tag so model output is
reproducible by version.
"""

from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "saturn-insight-v2"

_SYSTEM_COLUMN = f"""You are analysing a single column from a dataset profile emitted by saturn, a deterministic dataset dissector. The audience is an analyst who has not seen the dataset before.
Return a single JSON object with these keys:
- narrative: string, 2-4 sentences, plain prose. Lead with what the column likely IS, then what's surprising about its values.
- confidence: "high" | "medium" | "low".
- evidence_keys: array of stat keys you used.
- role: pick one of "identifier" | "label" | "feature" | "metadata" | "free_text" | "timestamp" | "numeric_target" | "foreign_key" | "other". Drives a one-word chip on the column heading.
- treatment: one short sentence on how to handle this column downstream (examples: "tokenize and embed before modelling"; "drop, near-unique"; "log-transform before regression"; "left-join on this id").
Rules:
- Only cite numbers that appear verbatim in the evidence payload.
- Never speculate beyond the evidence. If a signal is missing, say so.
- Flag any signal that would surprise an analyst (severe skew, drift, duplicates, language mix).
- No filler prose. No apologies. No disclaimers.
Tag: {PROMPT_VERSION}
"""

_SYSTEM_DATASET = f"""You are summarising the top-level shape of a dataset from saturn's findings. The audience is a non-specialist analyst who needs to know what to look at first.
Return a single JSON object with these keys:
- narrative: 3-6 sentences. Open with what the dataset is, then the 1-2 things worth a closer look.
- confidence: "high" | "medium" | "low".
- evidence_keys: array of stat keys you used.
- featured_charts: array of 3 to 5 objects of shape {{"column": str, "kind": str, "caption": str}}. `kind` must be one of "histogram" | "bar" | "donut" | "length" depending on what fits the column. Pick columns that tell the most about the dataset; the caption is one sentence saying what to look for.
Rules:
- Never invent numbers. Only cite values present in the evidence payload.
- featured_charts must reference columns that actually exist in the evidence.
- Do not pick columns whose only signal is "near_unique" or "all_null" — they make boring charts.
- Strict JSON: no trailing commas, every key:value separated by a single comma.
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

_SYSTEM_COMPARE_COLUMN = f"""You are comparing one column across two dataset slices profiled by saturn.
Return a single JSON object with these keys: narrative (string, 2-4 sentences focused on divergence between A and B), confidence ("high"|"medium"|"low"), evidence_keys (array of stat keys you used).
Rules:
- Only cite numbers that appear verbatim in the evidence payload. A side has label `a.label`, B side has label `b.label`. Refer to them by label, not "A"/"B".
- Lead with the most consequential divergence. If sides agree on everything, say so and stop.
- When a jaccard is present (language_jaccard, top_value_jaccard) and below 0.7, call it out explicitly.
- No filler, no disclaimers, no speculation beyond the evidence.
Tag: {PROMPT_VERSION}
"""

_SYSTEM_COMPARE_DATASET = f"""You are summarising a pairwise dataset comparison from saturn.
Return a single JSON object with these keys: narrative (string, 3-6 sentences), confidence ("high"|"medium"|"low"), evidence_keys (array of stat keys you used), hotspots (array of column names most worth a closer look).
Rules:
- Refer to the two sides by their labels (`a_label`, `b_label`), not "A"/"B".
- Pick the 3-5 columns that would change a downstream decision.
- Do not invent numbers. Only cite values present in the evidence.
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


def build_compare_column_prompt(evidence: dict[str, Any]) -> tuple[str, str]:
    user = f"Pair evidence:\n{_as_json(evidence)}"
    return _SYSTEM_COMPARE_COLUMN, user


def build_compare_dataset_prompt(evidence: dict[str, Any]) -> tuple[str, str]:
    user = f"Compare-dataset evidence:\n{_as_json(evidence)}"
    return _SYSTEM_COMPARE_DATASET, user
