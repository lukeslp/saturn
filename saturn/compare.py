"""Pairwise dataset comparison.

Compare two polars DataFrames column-by-column. The common schema is inferred
from the union of columns; where columns appear in only one side, the diff row
carries a 'missing' flag. Per-column deltas are summarised in a
`ColumnComparison` dataclass that the renderer consumes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .profilers import ProfileResult, profile_dataframe

if TYPE_CHECKING:
    import polars as pl


@dataclass
class ColumnComparison:
    column: str
    kind: str
    a: ProfileResult | None
    b: ProfileResult | None
    delta: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "kind": self.kind,
            "a": self.a.to_dict() if self.a else None,
            "b": self.b.to_dict() if self.b else None,
            "delta": self.delta,
            "notes": self.notes,
        }


@dataclass
class CompareSide:
    label: str
    source: str
    row_count: int
    schema: dict[str, str]
    language_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompareReport:
    a: CompareSide
    b: CompareSide
    columns: list[ColumnComparison]
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "a": self.a.to_dict(),
            "b": self.b.to_dict(),
            "columns": [c.to_dict() for c in self.columns],
            "divergences": self.divergence_summary(),
            "generated_at": self.generated_at,
        }

    def divergence_summary(self, k: int = 6) -> list[dict[str, Any]]:
        """Return the top-K columns ranked by a composite divergence score.

        Score combines (whichever apply): normalised mean/length delta,
        normalised null-rate delta, 1 - top-value jaccard, 1 - language
        jaccard, normalised entropy delta. Every term is capped at 1.0 so a
        single runaway value does not dominate the ranking.
        """
        scored: list[tuple[float, ColumnComparison, list[str]]] = []
        for c in self.columns:
            if c.a is None or c.b is None:
                continue
            score = 0.0
            terms: list[str] = []
            d = c.delta or {}

            nd = abs(d.get("null_rate_delta") or 0.0)
            if nd > 0.02:
                score += min(nd, 1.0)
                terms.append(f"null {nd:+.0%}")

            if "mean_a" in d:
                base = max(abs(d.get("mean_a") or 0.0), 1e-9)
                diff = abs(d.get("mean_delta") or 0.0) / base
                if diff > 0.05:
                    score += min(diff, 1.0)
                    terms.append(f"mean {d['mean_delta']:+.2f}")

            if "len_mean_a" in d:
                base = max(abs(d.get("len_mean_a") or 0.0), 1e-9)
                diff = abs(d.get("len_mean_delta") or 0.0) / base
                if diff > 0.05:
                    score += min(diff, 1.0)
                    terms.append(f"len_mean {d['len_mean_delta']:+.0f}")

            entropy_a = d.get("entropy_a")
            entropy_b = d.get("entropy_b")
            if isinstance(entropy_a, (int, float)) and isinstance(entropy_b, (int, float)):
                base = max(abs(entropy_a), 1e-9)
                diff = abs(entropy_b - entropy_a) / base
                if diff > 0.05:
                    score += min(diff, 1.0)
                    terms.append(f"entropy Δ {(entropy_b - entropy_a):+.2f}")

            lj = d.get("language_jaccard")
            if lj is not None and lj < 0.8:
                score += 1.0 - lj
                terms.append(f"lang-jaccard {lj:.2f}")
            tj = d.get("top_value_jaccard")
            if tj is not None and tj < 0.5:
                score += 1.0 - tj
                terms.append(f"top-val-jaccard {tj:.2f}")

            if c.notes:  # schema mismatch is always a divergence
                score += 0.5
                terms.append("schema mismatch")

            if score > 0:
                scored.append((score, c, terms))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            {"column": c.column, "kind": c.kind, "score": round(s, 3), "signals": terms}
            for s, c, terms in scored[:k]
        ]


def _safe(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delta(a: ProfileResult, b: ProfileResult, kind: str) -> dict[str, Any]:
    """Compute the comparable summary across two profile results."""
    d: dict[str, Any] = {
        "row_count_a": a.n,
        "row_count_b": b.n,
        "null_rate_a": a.null_rate,
        "null_rate_b": b.null_rate,
        "null_rate_delta": b.null_rate - a.null_rate,
    }
    if a.n_unique is not None and b.n_unique is not None:
        d["n_unique_a"] = a.n_unique
        d["n_unique_b"] = b.n_unique
        d["n_unique_delta"] = b.n_unique - a.n_unique

    if kind == "numeric":
        for key in ("mean", "median", "std", "q1", "q3", "min", "max", "outlier_rate", "skew"):
            va, vb = _safe(a.stats.get(key)), _safe(b.stats.get(key))
            if va is not None and vb is not None:
                d[f"{key}_a"] = va
                d[f"{key}_b"] = vb
                d[f"{key}_delta"] = vb - va
    elif kind == "text":
        for key in ("len_mean", "len_median", "len_p95", "word_mean", "duplicate_rate", "vocab_size"):
            va, vb = _safe(a.stats.get(key)), _safe(b.stats.get(key))
            if va is not None and vb is not None:
                d[f"{key}_a"] = va
                d[f"{key}_b"] = vb
                d[f"{key}_delta"] = vb - va
        # top-values jaccard
        top_a = set(k for k, _ in a.extras.get("top_values", []) or [])
        top_b = set(k for k, _ in b.extras.get("top_values", []) or [])
        if top_a or top_b:
            union = top_a | top_b
            d["top_value_jaccard"] = len(top_a & top_b) / len(union) if union else 0.0
        # language mix jaccard
        lang_a = {k for k in (a.extras.get("language_counts") or {}) if not k.startswith("__")}
        lang_b = {k for k in (b.extras.get("language_counts") or {}) if not k.startswith("__")}
        if lang_a or lang_b:
            union = lang_a | lang_b
            d["language_jaccard"] = len(lang_a & lang_b) / len(union) if union else 0.0
            d["languages_only_a"] = sorted(lang_a - lang_b)[:10]
            d["languages_only_b"] = sorted(lang_b - lang_a)[:10]
        # vocab jaccard (top words)
        words_a = set(k for k, _ in a.extras.get("top_words", []) or [])
        words_b = set(k for k, _ in b.extras.get("top_words", []) or [])
        if words_a or words_b:
            d["top_word_jaccard"] = len(words_a & words_b) / len(words_a | words_b) if (words_a or words_b) else 0.0
    elif kind in {"categorical", "boolean"}:
        d["top_value_a"] = a.stats.get("top_value")
        d["top_value_b"] = b.stats.get("top_value")
        d["entropy_a"] = a.stats.get("entropy")
        d["entropy_b"] = b.stats.get("entropy")
        if a.stats.get("entropy") is not None and b.stats.get("entropy") is not None:
            d["entropy_delta"] = b.stats["entropy"] - a.stats["entropy"]
        # value overlap
        set_a = set(k for k, _ in a.extras.get("top_values", []) or [])
        set_b = set(k for k, _ in b.extras.get("top_values", []) or [])
        if set_a or set_b:
            d["top_value_jaccard"] = len(set_a & set_b) / len(set_a | set_b) if (set_a or set_b) else 0.0
    return d


def _shared_schema(
    schema_a: dict[str, str], schema_b: dict[str, str]
) -> dict[str, str]:
    """Prefer the more specific kind when the two adapters disagree."""
    priority = {
        "unknown": 0,
        "text": 1,
        "categorical": 2,
        "boolean": 3,
        "numeric": 4,
    }
    merged: dict[str, str] = {}
    for col in {*schema_a.keys(), *schema_b.keys()}:
        a_kind = schema_a.get(col, "unknown")
        b_kind = schema_b.get(col, "unknown")
        merged[col] = a_kind if priority[a_kind] >= priority[b_kind] else b_kind
    return merged


def compare_dataframes(
    df_a: "pl.DataFrame",
    df_b: "pl.DataFrame",
    *,
    label_a: str,
    label_b: str,
    source_a: str,
    source_b: str,
    schema_a: dict[str, str] | None = None,
    schema_b: dict[str, str] | None = None,
    seed: int = 42,
) -> CompareReport:
    """Profile both frames and diff every column."""
    from datetime import datetime, timezone

    from .ingestion import _schema_from_dataframe  # type: ignore

    schema_a = schema_a or _schema_from_dataframe(df_a).columns
    schema_b = schema_b or _schema_from_dataframe(df_b).columns
    union = _shared_schema(schema_a, schema_b)

    # profile_dataframe uses the passed schema verbatim so both sides use the union
    results_a = {r.column: r for r in profile_dataframe(df_a, union, sample_seed=seed)}
    results_b = {r.column: r for r in profile_dataframe(df_b, union, sample_seed=seed)}

    columns: list[ColumnComparison] = []
    for col, kind in union.items():
        a_res = results_a.get(col)
        b_res = results_b.get(col)
        notes: list[str] = []
        delta: dict[str, Any] = {}
        if a_res is None:
            notes.append(f"absent in {label_a}")
        if b_res is None:
            notes.append(f"absent in {label_b}")
        if a_res is not None and b_res is not None:
            delta = _delta(a_res, b_res, kind)
        columns.append(
            ColumnComparison(column=col, kind=kind, a=a_res, b=b_res, delta=delta, notes=notes)
        )

    def _merged_langs(results: dict[str, ProfileResult]) -> dict[str, int]:
        merged: dict[str, int] = {}
        for r in results.values():
            if r.kind != "text":
                continue
            for lang, count in (r.extras.get("language_counts") or {}).items():
                if lang.startswith("__"):
                    continue
                merged[lang] = merged.get(lang, 0) + int(count)
        return merged

    report = CompareReport(
        a=CompareSide(
            label=label_a,
            source=source_a,
            row_count=df_a.height,
            schema=schema_a,
            language_counts=_merged_langs(results_a),
        ),
        b=CompareSide(
            label=label_b,
            source=source_b,
            row_count=df_b.height,
            schema=schema_b,
            language_counts=_merged_langs(results_b),
        ),
        columns=columns,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return report


def split_dataframe(
    df: "pl.DataFrame",
    column: str,
    values: list[str] | None = None,
) -> list[tuple[str, "pl.DataFrame"]]:
    """Partition a polars frame by a column value.

    If `values` is None, pick the two most frequent distinct values — that's
    the common case for a curated/firehose-style column.
    """
    import polars as pl

    if column not in df.columns:
        raise ValueError(f"Column {column!r} not present in dataset")

    if values is None:
        vc = df[column].value_counts(sort=True).head(2)
        count_col = "count" if "count" in vc.columns else vc.columns[-1]
        values = [str(row[0]) for row in vc.iter_rows()]
        if len(values) < 2:
            raise ValueError(
                f"Column {column!r} has only {len(values)} distinct value(s); cannot compare"
            )

    return [(v, df.filter(pl.col(column).cast(pl.Utf8) == v)) for v in values]
