"""Project `ReportData` into a compact, model-ready dict.

The insight pass NEVER sees raw user rows. Only per-column aggregates that
already appear in the public JSON findings sidecar are forwarded — same surface
a human reader would already have.

Two entry points:
- `column_evidence(report, column)` — one column's shape as a pruned dict.
- `dataset_evidence(report)`        — the full dataset shape, columns ordered
                                      by a coarse interestingness heuristic.
"""

from __future__ import annotations

import os
from typing import Any

from ..report import ReportData

# Deployment-wide redaction switch. Lets a public viewer force-redact literal
# cell values without threading the --no-evidence-values flag through every
# form / command (compare mode in particular has no flag).
REDACT_ENV = "SATURN_REDACT_EVIDENCE_VALUES"


def redact_values_from_env() -> bool:
    return os.environ.get(REDACT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

# Top-K literal values/words forwarded per column (count cap).
_TOP_K = 10

# Per-value byte cap on the literal user strings (`top_values` / `top_words`)
# forwarded to the model. Without it, one column of long text values can blow
# the provider context window (a real holdout dataset projected to 1.67M
# tokens). 200 bytes keeps each value legible to the model while bounding the
# worst-case payload size. Strings under the cap pass through unchanged.
_VALUE_BYTE_CAP = 200

# Stat keys whose value is a literal cell value (not an aggregate). These are
# the privacy-sensitive entries inside `stats`: withheld entirely when redacting,
# byte-capped like top_values otherwise. Categorical/boolean columns set this to
# the single most-frequent verbatim value.
_LITERAL_STAT_KEYS = ("top_value",)


def _prune_language_counts(counts: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in counts.items() if not k.startswith("__")}


def _cap_str(value: Any, cap: int = _VALUE_BYTE_CAP) -> str:
    """Truncate a value to at most `cap` UTF-8 bytes, on a codepoint boundary."""
    text = value if isinstance(value, str) else str(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= cap:
        return text
    return encoded[:cap].decode("utf-8", errors="ignore") + "…"


def project_stats(stats: dict[str, Any], *, redact_values: bool) -> dict[str, Any]:
    """Copy `stats`, scrubbing or byte-capping its literal-value entries.

    `stats` is mostly aggregates (mean, entropy, len_mean), but categorical and
    boolean columns also stash a literal `top_value`. Redaction drops those
    entries; otherwise they are byte-capped like the rest of the evidence values.
    Shared by the single-dataset and compare projections.
    """
    out = dict(stats)
    for key in _LITERAL_STAT_KEYS:
        if key not in out:
            continue
        if redact_values:
            out.pop(key, None)
        elif isinstance(out[key], str):
            out[key] = _cap_str(out[key])
    return out


def _cap_pairs(pairs: Any, cap: int = _VALUE_BYTE_CAP) -> list:
    """Cap a (value, count) ranking to top-K, byte-capping each value string.

    Short values keep their original type (tuples stay tuples) so the model-ready
    shape is unchanged for typical low-cardinality columns.
    """
    out: list = []
    for item in list(pairs)[:_TOP_K]:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            label, count = item
            capped = _cap_str(label, cap)
            out.append((capped, count) if isinstance(item, tuple) else [capped, count])
        else:
            out.append(_cap_str(item, cap))
    return out


def column_evidence(
    report: ReportData, column: str, *, redact_values: bool = False
) -> dict[str, Any]:
    """Project one column's shape into a model-ready dict.

    `redact_values=True` drops the literal user strings (`top_values` /
    `top_words`) entirely: the privacy switch behind `--no-evidence-values` /
    `SATURN_REDACT_EVIDENCE_VALUES`. Aggregates (counts, stats, language mix)
    are always forwarded; only the verbatim cell values are withheld.
    """
    result = next((r for r in report.results if r.column == column), None)
    if result is None:
        raise KeyError(f"column {column!r} not in report")
    ev: dict[str, Any] = {
        "column": result.column,
        "kind": result.kind,
        "n": result.n,
        "null_rate": round(result.null_rate, 4),
        "n_unique": result.n_unique,
        "stats": project_stats(result.stats, redact_values=redact_values),
        "alerts": [a.code for a in result.alerts],
    }
    langs = result.extras.get("language_counts")
    if isinstance(langs, dict):
        pruned = _prune_language_counts(langs)
        if pruned:
            ev["language_counts"] = pruned
    if redact_values:
        return ev
    top_values = result.extras.get("top_values")
    if top_values:
        ev["top_values"] = _cap_pairs(top_values)
    top_words = result.extras.get("top_words")
    if top_words:
        ev["top_words"] = _cap_pairs(top_words)
    return ev


def dataset_evidence(
    report: ReportData, *, redact_values: bool = False
) -> dict[str, Any]:
    by_interest = sorted(
        report.results,
        key=lambda r: (-len(r.alerts), -(r.null_rate or 0.0), r.column),
    )
    return {
        "source": report.meta.source,
        "row_count": report.meta.row_count,
        "column_count": len(report.results),
        "kinds": {r.column: r.kind for r in report.results},
        "columns": [
            column_evidence(report, r.column, redact_values=redact_values)
            for r in by_interest
        ],
    }
