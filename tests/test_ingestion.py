from __future__ import annotations

import json
from pathlib import Path

from saturn.ingestion import FileAdapter, adapter_for, reservoir_sample


def _write_jsonl(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "sample.jsonl"
    with p.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


def test_file_adapter_schema_and_iter(tmp_path: Path):
    rows = [{"x": i, "label": "a" if i % 2 else "b", "note": f"row {i}"} for i in range(20)]
    p = _write_jsonl(tmp_path, rows)

    adapter = FileAdapter(p)
    schema = adapter.schema()
    assert set(schema.columns) == {"x", "label", "note"}
    assert schema.columns["x"] == "numeric"
    # `label` has 2 values → categorical; `note` has 20 unique short strings but cardinality threshold is 50 → categorical too
    assert schema.columns["label"] == "categorical"

    batches = list(adapter.iter_batches(batch_size=8))
    flattened = [r for batch in batches for r in batch]
    assert len(flattened) == 20
    assert adapter.row_count() == 20


def test_reservoir_sample_seeded(tmp_path: Path):
    rows = [{"i": i} for i in range(2000)]

    def batches():
        for start in range(0, 2000, 100):
            yield rows[start : start + 100]

    s1, seen1 = reservoir_sample(batches(), n=100, seed=7)
    s2, seen2 = reservoir_sample(batches(), n=100, seed=7)
    assert seen1 == seen2 == 2000
    assert len(s1) == len(s2) == 100
    assert [r["i"] for r in s1] == [r["i"] for r in s2]


def test_adapter_for_dispatch(tmp_path: Path):
    p = _write_jsonl(tmp_path, [{"x": 1}])
    assert isinstance(adapter_for(str(p)), FileAdapter)
    # HF dispatch is string-based and should not touch the network just to construct the adapter
    hf = adapter_for("hf://nobody/whatever")
    assert type(hf).__name__ == "HFAdapter"
    assert hf.repo_id == "nobody/whatever"
