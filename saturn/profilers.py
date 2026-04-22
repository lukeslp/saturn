"""Column profilers.

Each profiler accepts a coarse column type (numeric / text / categorical /
boolean / unknown) and produces a `ProfileResult` — a dataclass holding the
stats the report template understands.

Profilers are registered against types. Adding a new column type is:
    1. write `NewProfiler(BaseProfiler)` with a class-level `accepts` set
    2. call `@register_profiler`
    3. template and chart builder get a new section for free
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Iterable


@dataclass
class Alert:
    level: str  # 'info' | 'warn' | 'error'
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProfileResult:
    column: str
    kind: str
    n: int = 0
    n_null: int = 0
    n_unique: int | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)

    @property
    def null_rate(self) -> float:
        return self.n_null / self.n if self.n else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["null_rate"] = self.null_rate
        return d


def _iter_values(column: str, batches: Iterable[list[dict[str, Any]]]):
    for batch in batches:
        for row in batch:
            yield row.get(column)


class BaseProfiler(ABC):
    accepts: ClassVar[set[str]] = set()

    @classmethod
    def handles(cls, kind: str) -> bool:
        return kind in cls.accepts

    @abstractmethod
    def profile(
        self, column: str, values: Iterable[Any]
    ) -> ProfileResult: ...


_REGISTRY: list[type[BaseProfiler]] = []


def register_profiler(cls: type[BaseProfiler]) -> type[BaseProfiler]:
    _REGISTRY.append(cls)
    return cls


def profiler_for(kind: str) -> BaseProfiler | None:
    for cls in _REGISTRY:
        if cls.handles(kind):
            return cls()
    return None


# ---------- numeric ----------------------------------------------------------


@register_profiler
class NumericProfiler(BaseProfiler):
    accepts: ClassVar[set[str]] = {"numeric"}

    def profile(self, column: str, values: Iterable[Any]) -> ProfileResult:
        import numpy as np
        from scipy import stats as scs

        arr_all: list[float] = []
        n = 0
        n_null = 0
        for v in values:
            n += 1
            if v is None or (isinstance(v, float) and math.isnan(v)):
                n_null += 1
                continue
            try:
                arr_all.append(float(v))
            except (TypeError, ValueError):
                n_null += 1

        result = ProfileResult(column=column, kind="numeric", n=n, n_null=n_null)
        if not arr_all:
            result.alerts.append(Alert("warn", "all_null", "column is entirely null or non-numeric"))
            return result

        a = np.asarray(arr_all, dtype=float)
        result.n_unique = int(np.unique(a).size)
        q1, q3 = np.quantile(a, [0.25, 0.75])
        iqr = q3 - q1
        outlier_mask = (a < q1 - 1.5 * iqr) | (a > q3 + 1.5 * iqr)
        skew = float(scs.skew(a)) if a.size > 2 else 0.0
        kurt = float(scs.kurtosis(a)) if a.size > 3 else 0.0

        result.stats = {
            "min": float(a.min()),
            "max": float(a.max()),
            "mean": float(a.mean()),
            "median": float(np.median(a)),
            "std": float(a.std(ddof=1)) if a.size > 1 else 0.0,
            "q1": float(q1),
            "q3": float(q3),
            "iqr": float(iqr),
            "skew": skew,
            "kurtosis": kurt,
            "n_outliers": int(outlier_mask.sum()),
            "outlier_rate": float(outlier_mask.mean()),
            "zero_rate": float((a == 0).mean()),
        }
        hist_counts, hist_edges = np.histogram(a, bins=min(40, max(5, int(math.sqrt(a.size)))))
        result.extras["histogram"] = {
            "counts": hist_counts.tolist(),
            "edges": hist_edges.tolist(),
        }
        # sample for chart + llm inspection
        sample_idx = np.random.default_rng(42).choice(a.size, size=min(500, a.size), replace=False)
        result.extras["sample"] = a[np.sort(sample_idx)].tolist()

        if abs(skew) > 2:
            result.alerts.append(Alert("info", "high_skew", f"skew={skew:+.2f}"))
        if result.stats["outlier_rate"] > 0.05:
            result.alerts.append(
                Alert("warn", "outliers", f"{result.stats['outlier_rate']:.1%} rows beyond 1.5 IQR")
            )
        if result.null_rate > 0.2:
            result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
        if result.n_unique == 1:
            result.alerts.append(Alert("info", "constant", "only one distinct value"))
        return result


# ---------- text -------------------------------------------------------------


_WORD_RE = re.compile(r"\S+")


@register_profiler
class TextProfiler(BaseProfiler):
    accepts: ClassVar[set[str]] = {"text"}

    def profile(self, column: str, values: Iterable[Any]) -> ProfileResult:
        import numpy as np

        lengths: list[int] = []
        word_counts: list[int] = []
        empty = 0
        n = 0
        n_null = 0
        lang_counter: Counter[str] = Counter()
        duplicate_counter: Counter[str] = Counter()
        vocab: Counter[str] = Counter()
        sample: list[str] = []

        try:
            import langdetect  # type: ignore

            langdetect.DetectorFactory.seed = 0
        except ImportError:
            langdetect = None

        for v in values:
            n += 1
            if v is None:
                n_null += 1
                continue
            s = str(v)
            if not s.strip():
                empty += 1
                n_null += 1
                continue
            lengths.append(len(s))
            words = _WORD_RE.findall(s)
            word_counts.append(len(words))
            for w in words[:32]:
                vocab[w.lower()] += 1
            duplicate_counter[s] += 1
            if len(sample) < 500:
                sample.append(s)
            if langdetect and 8 <= len(s) <= 400 and len(sample) <= 400:
                try:
                    lang_counter[langdetect.detect(s)] += 1
                except Exception:
                    lang_counter["unknown"] += 1

        result = ProfileResult(column=column, kind="text", n=n, n_null=n_null)
        if not lengths:
            result.alerts.append(Alert("warn", "all_empty", "column has no non-empty values"))
            return result

        lens = np.asarray(lengths)
        words = np.asarray(word_counts)
        dup_total = sum(c for c in duplicate_counter.values() if c > 1)
        top_values = duplicate_counter.most_common(10)
        top_words = vocab.most_common(25)

        # readability on a subset (textstat is not batch-friendly)
        fk_scores: list[float] = []
        try:
            import textstat  # type: ignore

            for s in sample[:200]:
                try:
                    fk_scores.append(float(textstat.flesch_reading_ease(s)))
                except Exception:
                    continue
        except ImportError:
            pass

        result.n_unique = len(duplicate_counter)
        result.stats = {
            "len_min": int(lens.min()),
            "len_max": int(lens.max()),
            "len_mean": float(lens.mean()),
            "len_median": float(np.median(lens)),
            "len_p95": float(np.quantile(lens, 0.95)),
            "word_mean": float(words.mean()),
            "word_median": float(np.median(words)),
            "n_empty": empty,
            "n_duplicates": int(dup_total),
            "duplicate_rate": float(dup_total / max(n - n_null, 1)),
            "vocab_size": len(vocab),
            "readability_flesch_mean": float(np.mean(fk_scores)) if fk_scores else None,
        }
        result.extras = {
            "length_histogram": _hist(lens, bins=40),
            "word_histogram": _hist(words, bins=30),
            "top_values": top_values,
            "top_words": top_words,
            "language_counts": dict(lang_counter.most_common(15)),
            "sample": sample[:50],
        }

        if result.stats["duplicate_rate"] > 0.2:
            result.alerts.append(
                Alert("warn", "duplicates", f"{result.stats['duplicate_rate']:.1%} duplicate strings")
            )
        if result.stats["len_p95"] < 20:
            result.alerts.append(
                Alert("info", "short_text", "95th-percentile length under 20 chars")
            )
        if result.null_rate > 0.2:
            result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
        if lang_counter and len(lang_counter) > 3:
            result.alerts.append(
                Alert(
                    "info",
                    "multilingual",
                    f"{len(lang_counter)} languages detected in sample",
                )
            )
        return result


# ---------- categorical ------------------------------------------------------


@register_profiler
class CategoricalProfiler(BaseProfiler):
    accepts: ClassVar[set[str]] = {"categorical", "boolean"}

    def profile(self, column: str, values: Iterable[Any]) -> ProfileResult:
        import numpy as np

        counts: Counter[Any] = Counter()
        n = 0
        n_null = 0
        for v in values:
            n += 1
            if v is None or v == "":
                n_null += 1
                continue
            counts[v] += 1

        result = ProfileResult(
            column=column,
            kind="categorical",
            n=n,
            n_null=n_null,
            n_unique=len(counts),
        )
        if not counts:
            result.alerts.append(Alert("warn", "all_null", "column has no values"))
            return result

        total = sum(counts.values())
        top = counts.most_common(20)
        top_rate = top[0][1] / total

        probabilities = np.array([c / total for c in counts.values()])
        entropy = float(-(probabilities * np.log2(probabilities)).sum())
        max_entropy = math.log2(len(counts)) if len(counts) > 1 else 0.0

        result.stats = {
            "top_value": str(top[0][0]),
            "top_rate": float(top_rate),
            "cardinality": len(counts),
            "entropy": entropy,
            "entropy_ratio": float(entropy / max_entropy) if max_entropy else 0.0,
        }
        result.extras = {
            "top_values": [(str(k), v) for k, v in top],
            "singletons": sum(1 for c in counts.values() if c == 1),
        }

        if top_rate > 0.95:
            result.alerts.append(Alert("warn", "imbalance", f"top value is {top_rate:.1%} of rows"))
        if result.extras["singletons"] / len(counts) > 0.5:
            result.alerts.append(
                Alert("info", "long_tail", f"{result.extras['singletons']} singleton categories")
            )
        if result.null_rate > 0.2:
            result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
        return result


# ---------- helpers ----------------------------------------------------------


def _hist(arr, bins: int) -> dict[str, list[float]]:
    import numpy as np

    if arr.size == 0:
        return {"counts": [], "edges": []}
    counts, edges = np.histogram(arr, bins=bins)
    return {"counts": counts.tolist(), "edges": edges.tolist()}


def profile_columns(
    schema: dict[str, str],
    sample: list[dict[str, Any]],
) -> list[ProfileResult]:
    """Run the registered profilers against an already-materialised sample.

    The sample is the reservoir sample from ingestion — profilers therefore see
    a bounded number of rows (default 2000). For datasets whose row count the
    caller also knows, set ProfileResult.n afterwards to the true count.
    """
    results: list[ProfileResult] = []
    for column, kind in schema.items():
        profiler = profiler_for(kind)
        if profiler is None:
            results.append(
                ProfileResult(
                    column=column,
                    kind=kind or "unknown",
                    n=len(sample),
                    alerts=[Alert("info", "skipped", f"no profiler for kind={kind}")],
                )
            )
            continue
        values = [row.get(column) for row in sample]
        results.append(profiler.profile(column, values))
    return results
