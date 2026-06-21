"""Project `CompareReport` into a model-ready pair payload.

Like `evidence.py` for the single-dataset path, this module forwards only
aggregates that already appear in the public findings JSON. No raw rows.
"""

from __future__ import annotations

from typing import Any

from ..compare import ColumnComparison, CompareReport
from ..profilers import ProfileResult
from .evidence import project_stats, redact_values_from_env


def _prune_lang(counts: dict[str, int] | None) -> dict[str, int]:
    if not isinstance(counts, dict):
        return {}
    return {k: v for k, v in counts.items() if not k.startswith("__")}


def _side_payload(label: str, p: ProfileResult | None) -> dict[str, Any] | None:
    if p is None:
        return None
    # Compare has no --no-evidence-values flag, so it honors the deployment-wide
    # env switch; the byte cap on literal stat values applies regardless.
    redact = redact_values_from_env()
    ev: dict[str, Any] = {
        "label": label,
        "n": p.n,
        "null_rate": round(p.null_rate, 4),
        "n_unique": p.n_unique,
        "stats": project_stats(p.stats, redact_values=redact),
        "alerts": [a.code for a in p.alerts],
    }
    langs = _prune_lang(p.extras.get("language_counts"))
    if langs:
        ev["language_counts"] = langs
    return ev


def compare_column_evidence(
    cc: ColumnComparison,
    *,
    a_label: str,
    b_label: str,
) -> dict[str, Any]:
    return {
        "column": cc.column,
        "kind": cc.kind,
        "a": _side_payload(a_label, cc.a),
        "b": _side_payload(b_label, cc.b),
        "delta": dict(cc.delta or {}),
        "notes": list(cc.notes or []),
    }


def compare_dataset_evidence(report: CompareReport) -> dict[str, Any]:
    divergences = report.divergence_summary(k=6)
    return {
        "a_label": report.a.label,
        "b_label": report.b.label,
        "a_row_count": report.a.row_count,
        "b_row_count": report.b.row_count,
        "column_count": len(report.columns),
        "divergences": divergences,
    }
