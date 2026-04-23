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

from typing import Any

from ..report import ReportData


def _prune_language_counts(counts: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in counts.items() if not k.startswith("__")}


def column_evidence(report: ReportData, column: str) -> dict[str, Any]:
    result = next((r for r in report.results if r.column == column), None)
    if result is None:
        raise KeyError(f"column {column!r} not in report")
    ev: dict[str, Any] = {
        "column": result.column,
        "kind": result.kind,
        "n": result.n,
        "null_rate": round(result.null_rate, 4),
        "n_unique": result.n_unique,
        "stats": dict(result.stats),
        "alerts": [a.code for a in result.alerts],
    }
    langs = result.extras.get("language_counts")
    if isinstance(langs, dict):
        pruned = _prune_language_counts(langs)
        if pruned:
            ev["language_counts"] = pruned
    top_values = result.extras.get("top_values")
    if top_values:
        ev["top_values"] = top_values[:10]
    top_words = result.extras.get("top_words")
    if top_words:
        ev["top_words"] = top_words[:10]
    return ev


def dataset_evidence(report: ReportData) -> dict[str, Any]:
    by_interest = sorted(
        report.results,
        key=lambda r: (-len(r.alerts), -(r.null_rate or 0.0), r.column),
    )
    return {
        "source": report.meta.source,
        "row_count": report.meta.row_count,
        "column_count": len(report.results),
        "kinds": {r.column: r.kind for r in report.results},
        "columns": [column_evidence(report, r.column) for r in by_interest],
    }
