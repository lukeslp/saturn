"""Data ingestion.

Two entry points per adapter:

- `load_dataframe()` — the default. Downloads (HF) or scans (local) the full
  corpus into a polars DataFrame. Vectorised profiling consumes this.
- `iter_batches()` — streaming fallback, used only when `--sample N` is set or
  a dataset is too large for a single polars frame.

Both adapters share a sample-based schema inference step so the caller can ask
'what kind of column is this' without paying full-load cost upfront.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:  # keep polars out of the import graph until it's needed
    import polars as pl


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
    def load_dataframe(self) -> "pl.DataFrame":
        """Return the full corpus as a polars DataFrame."""

    @abstractmethod
    def iter_batches(self, batch_size: int = 10_000) -> Iterator[list[dict[str, Any]]]: ...

    @abstractmethod
    def row_count(self) -> int | None: ...


# ---------- schema inference -------------------------------------------------


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
    """Short-text + low-cardinality gets promoted to categorical."""
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


def _schema_from_dataframe(df: "pl.DataFrame", cardinality_cap: int = 1000) -> Schema:
    """Refine coarse types using polars dtype + cheap unique counts.

    A text column is treated as categorical when either:
      * it has at most `cardinality_cap` distinct values, or
      * fewer than 1% of rows are unique (clear repetition across the corpus).
    That catches things like author handles in a 400K-row feed where 489
    distinct authors repeat thousands of times each.
    """
    import polars as pl

    schema = Schema()
    n = df.height
    for col in df.columns:
        s = df[col]
        dt = s.dtype
        if dt in (pl.Boolean,):
            schema.columns[col] = "boolean"
            continue
        if dt.is_numeric():
            schema.columns[col] = "numeric"
            continue
        if dt in (pl.Utf8, pl.String):
            unique = s.n_unique()
            unique_ratio = unique / n if n else 0
            if unique <= cardinality_cap or unique_ratio < 0.01:
                schema.columns[col] = "categorical"
            else:
                # URIs, CIDs, JSON blobs, mostly-unique text → text
                schema.columns[col] = "text"
            continue
        # list/struct/date columns: fall back to unknown for now — report will note
        schema.columns[col] = "unknown"
    return schema


# ---------- HuggingFace ------------------------------------------------------


class HFAdapter(SourceAdapter):
    """Load a HuggingFace dataset.

    `load_dataframe` does a bulk download + cache via the `datasets` library,
    then converts to polars. That is dramatically faster than streaming one row
    at a time when the whole corpus fits on disk.

    If the requested split does not exist for the dataset, every available
    split is loaded and concatenated — a deliberate default for a tool whose
    job is to profile the whole thing.
    """

    def __init__(self, repo_id: str, split: str | None = None, config: str | None = None) -> None:
        split_label = split or "all"
        self.source = f"hf://{repo_id}:{split_label}"
        self.repo_id = repo_id
        self.split = split  # None → auto-concat every split
        self.config = config
        self._schema: Schema | None = None
        self._row_count: int | None = None
        self._ds = None

    def _available_splits(self) -> list[str]:
        from datasets import get_dataset_split_names

        try:
            return list(get_dataset_split_names(self.repo_id, self.config))
        except Exception:
            # fall through to caller's fallback strategy
            return []

    def _resolve_split(self) -> str | list[str]:
        if self.split:
            return self.split
        available = self._available_splits()
        if not available:
            return "train"
        if len(available) == 1:
            return available[0]
        return available  # multi-split concat

    def _load(self):
        if self._ds is not None:
            return self._ds
        from datasets import Dataset, concatenate_datasets, load_dataset

        kwargs = {"verification_mode": "no_checks"}

        if self.split:
            self._ds = load_dataset(self.repo_id, self.config, split=self.split, **kwargs)
            self.source = f"hf://{self.repo_id}:{self.split}"
            return self._ds

        # split=None → load whatever the repo provides, concat every branch
        loaded = load_dataset(self.repo_id, self.config, **kwargs)
        if isinstance(loaded, Dataset):
            self._ds = loaded
            self.source = f"hf://{self.repo_id}"
        else:
            parts = list(loaded.values())
            names = list(loaded.keys())
            self._ds = parts[0] if len(parts) == 1 else concatenate_datasets(parts)
            self.source = f"hf://{self.repo_id}:[{'+'.join(names)}]"
        return self._ds

    def _load_streaming(self):
        from datasets import load_dataset

        target = self._resolve_split()
        if isinstance(target, list):
            # streaming concat: the caller will iterate each stream in turn
            datasets_list = [
                load_dataset(self.repo_id, self.config, split=s, streaming=True) for s in target
            ]

            def chained():
                for ds in datasets_list:
                    for row in ds:
                        yield row

            return chained()
        return load_dataset(self.repo_id, self.config, split=target, streaming=True)

    def schema(self) -> Schema:
        if self._schema is not None:
            return self._schema
        # head the first streaming split for quick schema inference
        ds = self._load_streaming()
        sample: list[dict[str, Any]] = []
        for i, row in enumerate(ds):
            sample.append(dict(row))
            if i >= 99:
                break
        self._schema = _infer_schema_from_sample(sample)
        return self._schema

    def load_dataframe(self) -> "pl.DataFrame":
        import polars as pl

        ds = self._load()
        try:
            df = pl.from_arrow(ds.data.table)
        except AttributeError:
            df = pl.from_pandas(ds.to_pandas())
        if isinstance(df, pl.Series):
            df = df.to_frame()
        self._row_count = df.height
        self._schema = _schema_from_dataframe(df)
        return df

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
            if not builder.info.splits:
                return None
            target = self._resolve_split()
            if isinstance(target, list):
                self._row_count = sum(
                    int(builder.info.splits[s].num_examples) for s in target if s in builder.info.splits
                )
            elif target in builder.info.splits:
                self._row_count = int(builder.info.splits[target].num_examples)
            return self._row_count
        except Exception:
            pass
        return None


# ---------- local files ------------------------------------------------------


class FileAdapter(SourceAdapter):
    """Scan a local CSV/JSONL/Parquet/SQLite file.

    `load_dataframe` goes through polars directly for csv/jsonl/parquet;
    SQLite falls back to DuckDB (polars has no direct SQLite reader).
    """

    def __init__(self, path: str | Path, table: str | None = None) -> None:
        self.path = Path(path)
        self.source = str(self.path)
        self.table = table
        self._schema: Schema | None = None
        self._row_count: int | None = None
        self._con = None
        self._df: "pl.DataFrame | None" = None

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

    def load_dataframe(self) -> "pl.DataFrame":
        import polars as pl

        if self._df is not None:
            return self._df

        ext = self.path.suffix.lower()
        try:
            if ext == ".parquet":
                df = pl.read_parquet(self.path)
            elif ext == ".csv":
                df = pl.read_csv(self.path, infer_schema_length=5_000)
            elif ext in {".jsonl", ".ndjson"}:
                df = pl.read_ndjson(self.path)
            elif ext == ".json":
                df = pl.read_json(self.path)
            else:
                # SQLite (and fallback): go through DuckDB's arrow export
                tbl = self._conn().execute(self._scan_sql()).fetch_arrow_table()
                df = pl.from_arrow(tbl)
                if isinstance(df, pl.Series):
                    df = df.to_frame()
        except Exception:
            # last-ditch: DuckDB can read almost anything
            tbl = self._conn().execute(self._scan_sql()).fetch_arrow_table()
            df = pl.from_arrow(tbl)
            if isinstance(df, pl.Series):
                df = df.to_frame()

        self._df = df
        self._row_count = df.height
        self._schema = _schema_from_dataframe(df)
        return df

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


# ---------- sampling + dispatch ---------------------------------------------


def reservoir_sample(
    batches: Iterator[list[dict[str, Any]]], n: int, seed: int = 42
) -> tuple[list[dict[str, Any]], int]:
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


def sample_from_dataframe(df: "pl.DataFrame", n: int, seed: int = 42) -> list[dict[str, Any]]:
    """Pull a deterministic n-row sample from an in-memory polars frame."""
    if df.height <= n:
        return df.to_dicts()
    return df.sample(n=n, seed=seed, shuffle=True).to_dicts()


def adapter_for(source: str, *, split: str | None = None, config: str | None = None) -> SourceAdapter:
    """`repo/name` or `hf://repo/name` → HF; everything else → local file.

    `split=None` means "every split concatenated" for HF datasets — the right
    default for a tool that dissects whole corpora.
    """
    if source.startswith("hf://"):
        return HFAdapter(source.removeprefix("hf://"), split=split, config=config)
    if "/" in source and not Path(source).exists():
        return HFAdapter(source, split=split, config=config)
    return FileAdapter(source)
