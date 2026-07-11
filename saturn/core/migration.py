"""Compatibility migration from Saturn 0.2 findings dictionaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .contract import ContractValidationError


def _engine(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": "saturn", "version": str(payload.get("saturn_version", "0.2"))}


def _descriptor(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {"source": str(raw.get("source", "")), "rowCount": raw.get("row_count"),
            "schema": deepcopy(raw.get("schema", {}))}


def _column(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(raw.get("column", "")), "kind": raw.get("kind", "unknown"),
        "count": int(raw.get("n", 0)), "nullCount": int(raw.get("n_null", 0)),
        "uniqueCount": raw.get("n_unique"), "nullRate": raw.get("null_rate", 0.0),
        "stats": deepcopy(raw.get("stats") or {}), "details": deepcopy(raw.get("extras") or {}),
        "alerts": deepcopy(raw.get("alerts") or []),
    }


def migrate_legacy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate a legacy profile or comparison findings object to contract v1."""
    if payload.get("contractVersion") == 1:
        return deepcopy(dict(payload))
    base = {"contractVersion": 1, "provenance": {}, "options": {},
            "engine": _engine(payload),
            "extensions": {"saturn.legacy": {"formatVersion": "0.2"}}}
    if "meta" in payload and "columns" in payload and "a" not in payload:
        meta = payload.get("meta") or {}
        base.update({
            "kind": "dataset_profile", "descriptor": _descriptor({**meta, "schema": payload.get("schema", {})}),
            "provenance": {"generatedAt": meta.get("generated_at")},
            "options": {"mode": meta.get("mode", "full"), "sampledRows": meta.get("sampled_rows", 0),
                        "seed": meta.get("seed", 42)},
            "columns": [_column(c) for c in payload.get("columns", [])],
            "alerts": [],
        })
        correlations = payload.get("correlations")
        if isinstance(correlations, Mapping):
            base["correlations"] = {"labels": deepcopy(correlations.get("labels", [])),
                                    "values": deepcopy(correlations.get("matrix", [])),
                                    "pairCounts": deepcopy(correlations.get("pair_counts", []))}
    elif all(key in payload for key in ("a", "b", "columns")):
        base.update({
            "kind": "dataset_comparison",
            "provenance": {"generatedAt": payload.get("generated_at")},
            "sides": {side: {"label": str(payload[side].get("label", side.upper())),
                              **_descriptor(payload[side])} for side in ("a", "b")},
            "columns": [{"name": str(c.get("column", "")),
                         "sideKinds": {"a": c.get("kind_a"), "b": c.get("kind_b")},
                         "compatible": bool(c.get("compatible", True)),
                         "profiles": {"a": _column(c["a"]) if c.get("a") else None,
                                      "b": _column(c["b"]) if c.get("b") else None},
                         "delta": deepcopy(c.get("delta") or {}),
                         "notes": deepcopy(c.get("notes") or [])}
                        for c in payload.get("columns", [])],
            "alerts": [],
        })
    else:
        raise ContractValidationError("not a recognized Saturn 0.2 findings document")
    for key in ("attributions", "insights", "notes", "language_counts", "divergences"):
        if key in payload:
            base["extensions"]["saturn.legacy"][key] = deepcopy(payload[key])
    return base
