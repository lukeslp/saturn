"""Tests for saturn.viewer.loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturn.viewer.loader import FindingsDoc, FindingsKind, list_findings, load_findings


def _write_profile_findings(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "meta": {
                    "source": "hf://t/d",
                    "row_count": 100,
                    "sampled_rows": 100,
                    "seed": 42,
                    "mode": "full",
                    "generated_at": "2026-04-22T00:00:00+00:00",
                },
                "schema": {"a": "numeric"},
                "language_counts": {},
                "notes": [],
                "columns": [
                    {
                        "column": "a",
                        "kind": "numeric",
                        "n": 100,
                        "n_null": 0,
                        "n_unique": 50,
                        "stats": {"mean": 1.0},
                        "extras": {},
                        "alerts": [],
                        "null_rate": 0.0,
                    }
                ],
            }
        )
    )


def _write_compare_findings(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "a": {
                    "label": "A",
                    "source": "hf://t/a",
                    "row_count": 100,
                    "schema": {"a": "numeric"},
                    "language_counts": {},
                },
                "b": {
                    "label": "B",
                    "source": "hf://t/b",
                    "row_count": 50,
                    "schema": {"a": "numeric"},
                    "language_counts": {},
                },
                "columns": [
                    {
                        "column": "a",
                        "kind": "numeric",
                        "a": None,
                        "b": None,
                        "delta": {},
                        "notes": [],
                    }
                ],
                "divergences": [],
                "generated_at": "2026-04-22T00:00:00+00:00",
            }
        )
    )


def test_load_profile_findings(tmp_path):
    p = tmp_path / "r.json"
    _write_profile_findings(p)
    doc = load_findings(p)
    assert isinstance(doc, FindingsDoc)
    assert doc.kind == FindingsKind.PROFILE
    assert doc.meta["source"] == "hf://t/d"
    assert len(doc.columns) == 1


def test_load_compare_findings(tmp_path):
    p = tmp_path / "c.json"
    _write_compare_findings(p)
    doc = load_findings(p)
    assert doc.kind == FindingsKind.COMPARE
    assert doc.a_label == "A"
    assert doc.b_label == "B"


def test_load_rejects_non_saturn_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"unrelated": true}')
    with pytest.raises(ValueError, match="not a saturn findings"):
        load_findings(p)


def test_list_findings_returns_sorted_by_mtime_desc(tmp_path):
    import os
    import time

    f1 = tmp_path / "one.json"
    f2 = tmp_path / "two.json"
    _write_profile_findings(f1)
    _write_compare_findings(f2)
    time.sleep(0.01)
    os.utime(f2, None)
    listed = list_findings(tmp_path)
    assert [d.id for d in listed] == ["two", "one"]


def test_list_findings_skips_non_saturn_json(tmp_path):
    _write_profile_findings(tmp_path / "good.json")
    (tmp_path / "bad.json").write_text('{"unrelated": true}')
    listed = list_findings(tmp_path)
    assert [d.id for d in listed] == ["good"]


def test_list_findings_empty_for_missing_dir(tmp_path):
    assert list_findings(tmp_path / "does-not-exist") == []
