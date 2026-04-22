"""Column profilers.

Two entry points:

- `profile_dataframe(df, schema)` — the default path. Polars-native, vectorised.
  Runs on the whole corpus. Memory-bounded (top-K caps on value_counts /
  vocabulary) so 400K-row-unique text columns do not blow up.
- `profile_columns(schema, sample)` — list-of-dict path. Used by unit tests and
  by the `--sample N` streaming mode. Same output shape.

Both return `ProfileResult` objects the report layer already understands.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    import polars as pl


# ---------- result types -----------------------------------------------------


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


# ---------- polars-native profilers ------------------------------------------


_TOP_VALUES_K = 20
_TOP_WORDS_K = 25
_TEXT_PREVIEW_K = 50
_LANG_SAMPLE_K = 5000
_CARD_WARN = 0.95
_NULL_WARN = 0.2


def profile_dataframe(
    df: "pl.DataFrame", schema: dict[str, str], *, sample_seed: int = 42
) -> list[ProfileResult]:
    """Profile every column of a polars DataFrame using its declared schema.

    A deterministic 5000-row sample is drawn once and reused for
    language-detection (the one metric that cannot go vectorised in a hurry).
    """
    import polars as pl

    results: list[ProfileResult] = []
    lang_sample_df = _sample(df, _LANG_SAMPLE_K, sample_seed)

    for column, kind in schema.items():
        if column not in df.columns:
            continue
        s = df[column]
        if kind == "numeric":
            results.append(_profile_numeric_series(column, s))
        elif kind == "text":
            results.append(
                _profile_text_series(
                    column,
                    s,
                    lang_sample=lang_sample_df[column] if column in lang_sample_df.columns else None,
                )
            )
        elif kind in {"categorical", "boolean"}:
            results.append(_profile_categorical_series(column, s))
        else:
            results.append(
                ProfileResult(
                    column=column,
                    kind=kind or "unknown",
                    n=df.height,
                    alerts=[Alert("info", "skipped", f"no profiler for kind={kind}")],
                )
            )
    return results


def _sample(df: "pl.DataFrame", n: int, seed: int) -> "pl.DataFrame":
    if df.height <= n:
        return df
    return df.sample(n=n, seed=seed, shuffle=True)


def _profile_numeric_series(column: str, s: "pl.Series") -> ProfileResult:
    import polars as pl

    n = s.len()
    n_null = s.null_count()
    result = ProfileResult(column=column, kind="numeric", n=n, n_null=n_null)

    clean = s.drop_nulls().cast(pl.Float64, strict=False).drop_nulls()
    if clean.len() == 0:
        result.alerts.append(Alert("warn", "all_null", "column is entirely null or non-numeric"))
        return result

    # vectorised stats
    minimum = float(clean.min())  # type: ignore[arg-type]
    maximum = float(clean.max())  # type: ignore[arg-type]
    mean = float(clean.mean())  # type: ignore[arg-type]
    std = float(clean.std(ddof=1)) if clean.len() > 1 else 0.0
    median = float(clean.median())  # type: ignore[arg-type]
    q1 = float(clean.quantile(0.25))  # type: ignore[arg-type]
    q3 = float(clean.quantile(0.75))  # type: ignore[arg-type]
    iqr = q3 - q1
    n_unique = clean.n_unique()
    zero_rate = float((clean == 0).sum() / clean.len())

    outlier_mask = (clean < q1 - 1.5 * iqr) | (clean > q3 + 1.5 * iqr)
    n_outliers = int(outlier_mask.sum())

    # skew + kurtosis via numpy; clean may be up to tens of millions → np once
    import numpy as np
    from scipy import stats as scs

    arr = clean.to_numpy()
    skew = float(scs.skew(arr)) if arr.size > 2 else 0.0
    kurt = float(scs.kurtosis(arr)) if arr.size > 3 else 0.0

    hist_bins = min(40, max(5, int(math.sqrt(arr.size))))
    hist_counts, hist_edges = np.histogram(arr, bins=hist_bins)

    # sample for correlation heatmap downstream (bounded)
    sample_size = min(500, arr.size)
    rng = np.random.default_rng(42)
    sample_idx = rng.choice(arr.size, size=sample_size, replace=False)
    chart_sample = arr[np.sort(sample_idx)].tolist()

    result.n_unique = n_unique
    result.stats = {
        "min": minimum,
        "max": maximum,
        "mean": mean,
        "median": median,
        "std": std,
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "skew": skew,
        "kurtosis": kurt,
        "n_outliers": n_outliers,
        "outlier_rate": float(n_outliers / clean.len()),
        "zero_rate": zero_rate,
    }
    result.extras = {
        "histogram": {"counts": hist_counts.tolist(), "edges": hist_edges.tolist()},
        "sample": chart_sample,
    }

    if abs(skew) > 2:
        result.alerts.append(Alert("info", "high_skew", f"skew={skew:+.2f}"))
    if result.stats["outlier_rate"] > 0.05:
        result.alerts.append(
            Alert("warn", "outliers", f"{result.stats['outlier_rate']:.1%} rows beyond 1.5 IQR")
        )
    if result.null_rate > _NULL_WARN:
        result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
    if n_unique == 1:
        result.alerts.append(Alert("info", "constant", "only one distinct value"))
    return result


_VOCAB_SAMPLE_K = 20_000
_VOCAB_ROW_CHAR_CAP = 500
_VOCAB_SKIP_AVG_LEN = 2000  # skip vocab entirely on JSON-blob-like columns
_NEAR_UNIQUE_FRAC = 0.95


def _profile_text_series(
    column: str, s: "pl.Series", *, lang_sample: "pl.Series | None" = None
) -> ProfileResult:
    import numpy as np
    import polars as pl

    n = s.len()
    n_null = s.null_count()
    result = ProfileResult(column=column, kind="text", n=n, n_null=n_null)

    clean = s.drop_nulls().cast(pl.Utf8, strict=False).drop_nulls()
    if clean.len() == 0:
        result.alerts.append(Alert("warn", "all_empty", "column has no non-empty values"))
        return result

    # vectorised length (always affordable)
    lengths = clean.str.len_chars()
    empty = int((clean.str.len_chars() == 0).sum())
    lens_np = lengths.drop_nulls().cast(pl.Int64).to_numpy()
    len_mean = float(lens_np.mean()) if lens_np.size else 0.0

    # word-count estimate (avoid regex on huge blobs; whitespace split is O(n) and fast)
    words_per_row = clean.str.split(" ").list.len()
    words_np = words_per_row.drop_nulls().cast(pl.Int64).to_numpy()

    # duplicate detection — polars native, handles near-unique columns fine
    n_unique = clean.n_unique()
    n_duplicates = clean.len() - n_unique
    near_unique = (n_unique / clean.len()) > _NEAR_UNIQUE_FRAC

    # top values: skip entirely when near-unique (would be 20 singletons)
    top_values: list[tuple[str, int]] = []
    if not near_unique:
        vc = clean.value_counts(sort=True).head(_TOP_VALUES_K)
        top_values = [(str(row[0]), int(row[1])) for row in vc.iter_rows()]

    # vocabulary: skip for JSON-blob-shaped columns; else sample + truncate + split
    top_words: list[tuple[str, int]] = []
    vocab_size = 0
    vocab_skipped_reason: str | None = None
    if len_mean > _VOCAB_SKIP_AVG_LEN:
        vocab_skipped_reason = f"avg row {int(len_mean)} chars — vocab expansion would be too costly"
    else:
        sample = _sample_series(clean, _VOCAB_SAMPLE_K, seed=41)
        if sample.len():
            # truncate each row before split so pathological long rows don't dominate
            truncated = sample.str.slice(0, _VOCAB_ROW_CHAR_CAP).str.to_lowercase()
            words_flat = truncated.str.split(" ").explode().drop_nulls()
            words_flat = words_flat.filter(words_flat.str.len_chars() > 0)
            if words_flat.len():
                vocab_vc = words_flat.value_counts(sort=True).head(_TOP_WORDS_K)
                top_words = [(str(row[0]), int(row[1])) for row in vocab_vc.iter_rows()]
                vocab_size = int(words_flat.n_unique())

    # language detection — sample-based, ~5000 rows. Skip ID-shaped columns
    # (URIs, CIDs, timestamps) where detection is noise.
    lang_counts: dict[str, int] = {}
    word_mean = float(words_np.mean()) if words_np.size else 0.0
    if lang_sample is not None and word_mean >= 3 and not near_unique:
        lang_counts = _detect_languages(lang_sample)

    # readability (bounded sample)
    fk_scores: list[float] = []
    try:
        import textstat  # type: ignore

        preview_readability = _sample_series(clean, 200, seed=43).to_list()
        for string in preview_readability:
            try:
                fk_scores.append(float(textstat.flesch_reading_ease(string[:1000])))
            except Exception:
                continue
    except ImportError:
        pass

    preview = _sample_series(clean, _TEXT_PREVIEW_K, seed=44).to_list()

    result.n_unique = n_unique
    result.stats = {
        "len_min": int(lens_np.min()) if lens_np.size else 0,
        "len_max": int(lens_np.max()) if lens_np.size else 0,
        "len_mean": len_mean,
        "len_median": float(np.median(lens_np)) if lens_np.size else 0.0,
        "len_p95": float(np.quantile(lens_np, 0.95)) if lens_np.size else 0.0,
        "word_mean": float(words_np.mean()) if words_np.size else 0.0,
        "word_median": float(np.median(words_np)) if words_np.size else 0.0,
        "n_empty": empty,
        "n_duplicates": int(n_duplicates),
        "duplicate_rate": float(n_duplicates / clean.len()),
        "vocab_size": vocab_size,
        "readability_flesch_mean": float(np.mean(fk_scores)) if fk_scores else None,
    }
    result.extras = {
        "length_histogram": _np_hist(lens_np, bins=40),
        "word_histogram": _np_hist(words_np, bins=30),
        "top_values": top_values,
        "top_words": top_words,
        "language_counts": lang_counts,
        "sample": preview,
        "language_sample_size": lang_sample.len() if lang_sample is not None else 0,
        "vocab_skipped": vocab_skipped_reason,
        "near_unique": near_unique,
    }

    if near_unique:
        result.alerts.append(
            Alert("info", "near_unique", f"{(n_unique / clean.len()):.1%} of rows are unique strings")
        )
    if result.stats["duplicate_rate"] > 0.2:
        result.alerts.append(
            Alert("warn", "duplicates", f"{result.stats['duplicate_rate']:.1%} duplicate strings")
        )
    if result.stats["len_p95"] < 20:
        result.alerts.append(Alert("info", "short_text", "95th-percentile length under 20 chars"))
    if result.null_rate > _NULL_WARN:
        result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
    if lang_counts and len(lang_counts) > 3:
        result.alerts.append(
            Alert("info", "multilingual", f"{len(lang_counts)} languages detected in sample")
        )
    if vocab_skipped_reason:
        result.alerts.append(Alert("info", "vocab_skipped", vocab_skipped_reason))
    return result


def _profile_categorical_series(column: str, s: "pl.Series") -> ProfileResult:
    import numpy as np
    import polars as pl

    n = s.len()
    n_null = s.null_count()
    result = ProfileResult(column=column, kind="categorical", n=n, n_null=n_null)

    clean = s.drop_nulls()
    if clean.len() == 0:
        result.alerts.append(Alert("warn", "all_null", "column has no values"))
        return result

    vc = clean.value_counts(sort=True)
    count_col = "count" if "count" in vc.columns else vc.columns[-1]
    total = int(vc[count_col].sum())
    cardinality = vc.height
    top = vc.head(_TOP_VALUES_K)
    top_values: list[tuple[str, int]] = [(str(row[0]), int(row[1])) for row in top.iter_rows()]
    top_rate = float(top_values[0][1] / total) if top_values else 0.0

    probs = vc[count_col].to_numpy().astype(float) / total
    entropy = float(-(probs * np.log2(probs + 1e-30)).sum())
    max_entropy = math.log2(cardinality) if cardinality > 1 else 0.0
    singletons = int((vc[count_col] == 1).sum())

    result.n_unique = cardinality
    result.stats = {
        "top_value": top_values[0][0] if top_values else "",
        "top_rate": top_rate,
        "cardinality": cardinality,
        "entropy": entropy,
        "entropy_ratio": float(entropy / max_entropy) if max_entropy else 0.0,
    }
    result.extras = {"top_values": top_values, "singletons": singletons}

    if top_rate > _CARD_WARN:
        result.alerts.append(Alert("warn", "imbalance", f"top value is {top_rate:.1%} of rows"))
    if cardinality and singletons / cardinality > 0.5:
        result.alerts.append(Alert("info", "long_tail", f"{singletons} singleton categories"))
    if result.null_rate > _NULL_WARN:
        result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
    return result


def _detect_languages(sample: "pl.Series") -> dict[str, int]:
    """Sample-based language detection. fasttext first, langdetect fallback."""
    strings = [s for s in sample.drop_nulls().to_list() if isinstance(s, str) and 8 <= len(s) <= 400]
    if not strings:
        return {}

    # fasttext is fast but optional — 50MB model + extra dep
    try:
        import fasttext  # type: ignore

        model_path = _ensure_fasttext_lid()
        if model_path:
            model = fasttext.load_model(model_path)
            labels, _ = model.predict(strings, k=1)
            counter: Counter[str] = Counter()
            for row in labels:
                if row:
                    counter[row[0].replace("__label__", "")] += 1
            return dict(counter.most_common(30))
    except ImportError:
        pass

    try:
        import langdetect  # type: ignore

        langdetect.DetectorFactory.seed = 0
    except ImportError:
        return {}

    counter: Counter[str] = Counter()
    for s in strings:
        try:
            counter[langdetect.detect(s)] += 1
        except Exception:
            counter["unknown"] += 1
    return dict(counter.most_common(30))


def _ensure_fasttext_lid() -> str | None:
    """Return a local path to the fasttext lid.176 model if available, else None.

    We do not auto-download: network on dreamer may not be assumed, and the
    user should opt into an extra 50MB dep. Place the model at
    `$SATURN_FASTTEXT_LID` or `./.cache/saturn/lid.176.bin` to enable.
    """
    import os
    from pathlib import Path

    env = os.environ.get("SATURN_FASTTEXT_LID")
    if env and Path(env).exists():
        return env
    default = Path(".cache/saturn/lid.176.bin")
    if default.exists():
        return str(default)
    return None


def _sample_series(s: "pl.Series", n: int, seed: int) -> "pl.Series":
    if s.len() <= n:
        return s
    return s.sample(n=n, seed=seed, shuffle=True)


def _np_hist(arr, bins: int) -> dict[str, list[float]]:
    import numpy as np

    if arr.size == 0:
        return {"counts": [], "edges": []}
    counts, edges = np.histogram(arr, bins=bins)
    return {"counts": counts.tolist(), "edges": edges.tolist()}


# ---------- list-of-dict profilers (tests + sample mode) ---------------------


_WORD_RE = re.compile(r"\S+")


def profile_columns(
    schema: dict[str, str],
    sample: list[dict[str, Any]],
) -> list[ProfileResult]:
    """Profile a materialised sample (used by `--sample N` and unit tests)."""
    results: list[ProfileResult] = []
    for column, kind in schema.items():
        values = [row.get(column) for row in sample]
        if kind == "numeric":
            results.append(_dict_numeric(column, values))
        elif kind == "text":
            results.append(_dict_text(column, values))
        elif kind in {"categorical", "boolean"}:
            results.append(_dict_categorical(column, values))
        else:
            results.append(
                ProfileResult(
                    column=column,
                    kind=kind or "unknown",
                    n=len(sample),
                    alerts=[Alert("info", "skipped", f"no profiler for kind={kind}")],
                )
            )
    return results


def _dict_numeric(column: str, values: Iterable[Any]) -> ProfileResult:
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
    result.extras["histogram"] = {"counts": hist_counts.tolist(), "edges": hist_edges.tolist()}
    sample_idx = np.random.default_rng(42).choice(a.size, size=min(500, a.size), replace=False)
    result.extras["sample"] = a[np.sort(sample_idx)].tolist()

    if abs(skew) > 2:
        result.alerts.append(Alert("info", "high_skew", f"skew={skew:+.2f}"))
    if result.stats["outlier_rate"] > 0.05:
        result.alerts.append(
            Alert("warn", "outliers", f"{result.stats['outlier_rate']:.1%} rows beyond 1.5 IQR")
        )
    if result.null_rate > _NULL_WARN:
        result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
    if result.n_unique == 1:
        result.alerts.append(Alert("info", "constant", "only one distinct value"))
    return result


def _dict_text(column: str, values: Iterable[Any]) -> ProfileResult:
    import numpy as np

    lengths: list[int] = []
    word_counts: list[int] = []
    empty = 0
    n = 0
    n_null = 0
    lang_counter: Counter[str] = Counter()
    duplicate_counter: Counter[str] = Counter()
    vocab: Counter[str] = Counter()
    sample_strings: list[str] = []

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
        if len(sample_strings) < 500:
            sample_strings.append(s)
        if langdetect and 8 <= len(s) <= 400 and len(sample_strings) <= 400:
            try:
                lang_counter[langdetect.detect(s)] += 1
            except Exception:
                lang_counter["unknown"] += 1
        # bounded memory: periodically trim the counter if it grows huge
        if len(duplicate_counter) > 200_000:
            duplicate_counter = Counter(dict(duplicate_counter.most_common(50_000)))
        if len(vocab) > 500_000:
            vocab = Counter(dict(vocab.most_common(100_000)))

    result = ProfileResult(column=column, kind="text", n=n, n_null=n_null)
    if not lengths:
        result.alerts.append(Alert("warn", "all_empty", "column has no non-empty values"))
        return result

    lens = np.asarray(lengths)
    words = np.asarray(word_counts)
    dup_total = sum(c for c in duplicate_counter.values() if c > 1)
    top_values = duplicate_counter.most_common(10)
    top_words = vocab.most_common(25)

    fk_scores: list[float] = []
    try:
        import textstat  # type: ignore

        for s in sample_strings[:200]:
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
        "length_histogram": _np_hist(lens, bins=40),
        "word_histogram": _np_hist(words, bins=30),
        "top_values": top_values,
        "top_words": top_words,
        "language_counts": dict(lang_counter.most_common(15)),
        "sample": sample_strings[:50],
    }

    if result.stats["duplicate_rate"] > 0.2:
        result.alerts.append(
            Alert("warn", "duplicates", f"{result.stats['duplicate_rate']:.1%} duplicate strings")
        )
    if result.stats["len_p95"] < 20:
        result.alerts.append(Alert("info", "short_text", "95th-percentile length under 20 chars"))
    if result.null_rate > _NULL_WARN:
        result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
    if lang_counter and len(lang_counter) > 3:
        result.alerts.append(
            Alert("info", "multilingual", f"{len(lang_counter)} languages detected in sample")
        )
    return result


def _dict_categorical(column: str, values: Iterable[Any]) -> ProfileResult:
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

    if top_rate > _CARD_WARN:
        result.alerts.append(Alert("warn", "imbalance", f"top value is {top_rate:.1%} of rows"))
    if result.extras["singletons"] / len(counts) > 0.5:
        result.alerts.append(
            Alert("info", "long_tail", f"{result.extras['singletons']} singleton categories")
        )
    if result.null_rate > _NULL_WARN:
        result.alerts.append(Alert("warn", "null_rate", f"{result.null_rate:.1%} null"))
    return result
