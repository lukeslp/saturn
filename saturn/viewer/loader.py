"""Load and classify saturn findings JSON files for the viewer.

The viewer is read-only: files on disk are the source of truth. This module
turns one JSON file into a validated `FindingsDoc`, and a directory into a
list of them sorted newest-first.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from saturn.core import validate_contract


class FindingsKind(str, enum.Enum):
    PROFILE = "profile"
    COMPARE = "compare"


@dataclass
class FindingsDoc:
    id: str
    path: Path
    kind: FindingsKind
    raw: dict[str, Any]
    artifact: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    columns: list[dict[str, Any]] | None = None
    a_label: str | None = None
    b_label: str | None = None
    has_notes: bool = False


def _classify(payload: dict[str, Any]) -> FindingsKind:
    if payload.get("contractVersion") == 1:
        validate_contract(payload)
        return (FindingsKind.PROFILE if payload["kind"] == "dataset_profile"
                else FindingsKind.COMPARE)
    if "meta" in payload and "columns" in payload and "a" not in payload:
        return FindingsKind.PROFILE
    if "a" in payload and "b" in payload and "columns" in payload:
        return FindingsKind.COMPARE
    raise ValueError("not a saturn findings document (missing expected top-level keys)")


def _legacy_column(column: dict[str, Any]) -> dict[str, Any]:
    return {
        "column": column["name"], "kind": column["kind"],
        "n": column["count"], "n_null": column["nullCount"],
        "n_unique": column["uniqueCount"], "null_rate": column["nullRate"],
        "stats": column["stats"], "extras": column["details"],
        "alerts": column["alerts"],
    }


def _view_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt validated contract v1 to the viewer's established display shape."""
    if payload.get("contractVersion") != 1:
        return payload
    generated_at = payload.get("provenance", {}).get("generatedAt") or ""
    legacy = payload.get("extensions", {}).get("saturn.legacy", {})
    common = {
        "saturn_version": payload["engine"]["version"],
        "generated_at": generated_at,
        "insights": legacy.get("insights", {}),
        "language_counts": legacy.get("language_counts", {}),
    }
    if payload["kind"] == "dataset_profile":
        descriptor = payload["descriptor"]
        options = payload["options"]
        return {
            **common,
            "meta": {
                "source": descriptor["source"], "row_count": descriptor["rowCount"],
                "sampled_rows": options.get("sampledRows", descriptor["rowCount"]),
                "seed": options.get("seed"), "mode": options.get("mode", "full"),
                "generated_at": generated_at,
            },
            "schema": descriptor["schema"],
            "columns": [_legacy_column(column) for column in payload["columns"]],
            "notes": legacy.get("notes", []),
        }
    sides = payload["sides"]
    columns = []
    for column in payload["columns"]:
        profiles = column["profiles"]
        columns.append({
            "column": column["name"],
            "kind": column["sideKinds"]["a"] or column["sideKinds"]["b"] or "unknown",
            "kind_a": column["sideKinds"]["a"], "kind_b": column["sideKinds"]["b"],
            "compatible": column["compatible"],
            "a": _legacy_column(profiles["a"]) if profiles["a"] else None,
            "b": _legacy_column(profiles["b"]) if profiles["b"] else None,
            "delta": column["delta"], "notes": column["notes"],
        })
    return {
        **common,
        "a": {"label": sides["a"]["label"], "source": sides["a"]["source"],
              "row_count": sides["a"]["rowCount"], "schema": sides["a"]["schema"]},
        "b": {"label": sides["b"]["label"], "source": sides["b"]["source"],
              "row_count": sides["b"]["rowCount"], "schema": sides["b"]["schema"]},
        "columns": columns, "divergences": legacy.get("divergences", []),
    }


def load_findings(path: Path) -> FindingsDoc:
    path = Path(path)
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"not a saturn findings document: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("not a saturn findings document (root is not an object)")

    kind = _classify(payload)
    view = _view_model(payload)
    doc = FindingsDoc(id=path.stem, path=path, kind=kind, raw=view, artifact=payload)
    if kind is FindingsKind.PROFILE:
        doc.meta = view["meta"]
        doc.columns = view["columns"]
    else:
        doc.a_label = view["a"].get("label", "A")
        doc.b_label = view["b"].get("label", "B")
    doc.has_notes = path.with_suffix(".notes.md").is_file()
    return doc


def list_findings(directory: Path) -> list[FindingsDoc]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    paths = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    docs: list[FindingsDoc] = []
    for p in paths:
        try:
            docs.append(load_findings(p))
        except ValueError:
            continue
    return docs
