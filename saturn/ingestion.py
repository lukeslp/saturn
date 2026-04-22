"""Data ingestion: HuggingFace streaming, local file scan, reservoir sampler.

Ingestion adapters expose three things: a schema (column -> dtype-hint), an
iterator over row-dict batches, and a row count estimate (may be None for
streams). Profilers consume those batches incrementally so a 400K-row corpus
never has to sit in memory all at once.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class Schema:
    """Column -> coarse type hint: 'numeric', 'text', 'categorical', 'boolean', 'unknown'."""

    columns: dict[str, str] = field(default_factory=dict)

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self.columns.items())


class SourceAdapter(ABC):
    """Abstract ingestion adapter."""

    source: str

    @abstractmethod
    def schema(self) -> Schema: ...

    @abstractmethod
    def iter_batches(self, batch_size: int = 10_000) -> Iterator[list[dict[str, Any]]]: ...

    @abstractmethod
    def row_count(self) -> int | None: ...


def _coarse_type(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "numeric"
    if isinstance(value, str):
        return "text"
    return "unknown"


def _infer_schema_from_sample(sample: list[dict[str, Any]]) -> Schema:
    """Heuristic: majority type across non-null values per column, then a short-text
    column with low cardinality becomes 'categorical'."""
    counts: dict[str, dict[str, int]] = {}
    values: dict[str, set[Any]] = {}
    for row in sample:
        for col, val in row.items():
            t = _coarse_type(val)
            counts.setdefault(col, {}).setdefault(t, 0)
            counts[col][t] += 1
            if val is not None and len(values.setdefault(col, set())) < 200:
                values[col].add(val)

    schema = Schema()
    for col, type_counts in counts.items():
        type_counts.pop("unknown", None)
        if not type_counts:
            schema.columns[col] = "unknown"
            continue
        primary = max(type_counts.items(), key=lambda kv: kv[1])[0]
        if primary == "text":
            distinct = len(values.get(col, set()))
            avg_len = (
                sum(len(v) for v in values[col] if isinstance(v, str)) / max(distinct, 1)
                if values.get(col)
                else 0
            )
            if distinct <= 50 and avg_len < 40:
                primary = "categorical"
        schema.columns[col] = primary
    return schema


class HFAdapter(SourceAdapter):
    """Stream a HuggingFace dataset without materialising the full table."""

    def __init__(self, repo_id: str, split: str = "train", config: str | None = None) -> None:
        self.source = f"hf://{repo_id}:{split}"
        self.repo_id = repo_id
        self.split = split
        self.config = config
        self._schema: Schema | None = None
        self._row_count: int | None = None

    def _load_streaming(self):
        from datasets import load_dataset

        return load_dataset(self.repo_id, self.config, split=self.split, streaming=True)

    def schema(self) -> Schema:
        if self._schema is not None:
            return self._schema
        ds = self._load_streaming()
        sample: list[dict[str, Any]] = []
        for i, row in enumerate(ds):
            sample.append(dict(row))
            if i >= 99:
                break
        self._schema = _infer_schema_from_sample(sample)
        return self._schema

    def iter_batches(self, batch_size: int = 10_000) -> Iterator[list[dict[str, Any]]]:
        ds = self._load_streaming()
        buffer: list[dict[str, Any]] = []
        for row in ds:
            buffer.append(dict(row))
            if len(buffer) >= batch_size:
                yield buffer
                buffer = []
        if buffer:
            yield buffer

    def row_count(self) -> int | None:
        if self._row_count is not None:
            return self._row_count
        try:
            from datasets import load_dataset_builder

            builder = load_dataset_builder(self.repo_id, self.config)
            info = builder.info.splits.get(self.split) if builder.info.splits else None
            if info is not None:
                self._row_count = int(info.num_examples)
                return self._row_count
        except Exception:
            pass
        return None


class FileAdapter(SourceAdapter):
    """Scan a local CSV/JSONL/Parquet/SQLite file via DuckDB."""

    def __init__(self, path: str | Path, table: str | None = None) -> None:
        self.path = Path(path)
        self.source = str(self.path)
        self.table = table
        self._schema: Schema | None = None
        self._row_count: int | None = None
        self._con = None

    def _conn(self):
        import duckdb

        if self._con is None:
            self._con = duckdb.connect(":memory:")
        return self._con

    def _scan_sql(self) -> str:
        ext = self.path.suffix.lower()
        if ext == ".parquet":
            return f"SELECT * FROM read_parquet('{self.path}')"
        if ext == ".csv":
            return f"SELECT * FROM read_csv_auto('{self.path}')"
        if ext in {".jsonl", ".ndjson"}:
            return f"SELECT * FROM read_json_auto('{self.path}', format='newline_delimited')"
        if ext == ".json":
            return f"SELECT * FROM read_json_auto('{self.path}')"
        if ext in {".db", ".sqlite", ".sqlite3"}:
            con = self._conn()
            con.execute("INSTALL sqlite; LOAD sqlite;")
            con.execute(f"ATTACH '{self.path}' AS s (TYPE sqlite);")
            table = self.table
            if table is None:
                rows = con.execute(
                    "SELECT name FROM s.sqlite_master WHERE type='table' LIMIT 1"
                ).fetchall()
                if not rows:
                    raise ValueError(f"No tables in SQLite file {self.path}")
                table = rows[0][0]
            return f"SELECT * FROM s.{table}"
        raise ValueError(f"Unsupported file type: {ext}")

    def schema(self) -> Schema:
        if self._schema is not None:
            return self._schema
        con = self._conn()
        sample_rows = con.execute(f"{self._scan_sql()} LIMIT 200").fetch_df().to_dict("records")
        self._schema = _infer_schema_from_sample(sample_rows)
        return self._schema

    def iter_batches(self, batch_size: int = 10_000) -> Iterator[list[dict[str, Any]]]:
        import duckdb

        con = self._conn()
        cursor = con.execute(self._scan_sql())
        while True:
            try:
                df = cursor.fetch_df_chunk(vectors_per_chunk=max(1, batch_size // 2048))
            except duckdb.InvalidInputException:
                break
            if df is None or df.empty:
                break
            yield df.to_dict("records")

    def row_count(self) -> int | None:
        if self._row_count is not None:
            return self._row_count
        try:
            con = self._conn()
            result = con.execute(f"SELECT COUNT(*) FROM ({self._scan_sql()})").fetchone()
            self._row_count = int(result[0]) if result else None
            return self._row_count
        except Exception:
            return None


def reservoir_sample(
    batches: Iterator[list[dict[str, Any]]], n: int, seed: int = 42
) -> tuple[list[dict[str, Any]], int]:
    """Algorithm R reservoir sampling over a batched iterator.

    Returns (sample, total_rows_seen). Exact stream size is recovered as a
    by-product, which lets the stats pass report real row counts without an
    extra scan.
    """
    rng = random.Random(seed)
    sample: list[dict[str, Any]] = []
    seen = 0
    for batch in batches:
        for row in batch:
            if len(sample) < n:
                sample.append(row)
            else:
                j = rng.randint(0, seen)
                if j < n:
                    sample[j] = row
            seen += 1
    return sample, seen


def adapter_for(source: str, *, split: str = "train", config: str | None = None) -> SourceAdapter:
    """Pick an adapter by convention: `repo/name` or `hf://repo/name` -> HF; otherwise local file."""
    if source.startswith("hf://"):
        return HFAdapter(source.removeprefix("hf://"), split=split, config=config)
    if "/" in source and not Path(source).exists():
        return HFAdapter(source, split=split, config=config)
    return FileAdapter(source)
