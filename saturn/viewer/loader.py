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


class FindingsKind(str, enum.Enum):
    PROFILE = "profile"
    COMPARE = "compare"


@dataclass
class FindingsDoc:
    id: str
    path: Path
    kind: FindingsKind
    raw: dict[str, Any]
    meta: dict[str, Any] | None = None
    columns: list[dict[str, Any]] | None = None
    a_label: str | None = None
    b_label: str | None = None


def _classify(payload: dict[str, Any]) -> FindingsKind:
    if "meta" in payload and "columns" in payload and "a" not in payload:
        return FindingsKind.PROFILE
    if "a" in payload and "b" in payload and "columns" in payload:
        return FindingsKind.COMPARE
    raise ValueError("not a saturn findings document (missing expected top-level keys)")


def load_findings(path: Path) -> FindingsDoc:
    path = Path(path)
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"not a saturn findings document: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("not a saturn findings document (root is not an object)")

    kind = _classify(payload)
    doc = FindingsDoc(id=path.stem, path=path, kind=kind, raw=payload)
    if kind is FindingsKind.PROFILE:
        doc.meta = payload["meta"]
        doc.columns = payload["columns"]
    else:
        doc.a_label = payload["a"].get("label", "A")
        doc.b_label = payload["b"].get("label", "B")
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
