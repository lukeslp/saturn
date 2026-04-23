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


def _numeric_stats(a) -> tuple[dict, dict]:
    """Given a numpy array of clean floats, return (stats, extras).

    Shared core for both `_profile_numeric_series` (polars) and `_dict_numeric`.
    Guards borrowed from the polars path:
    - scipy precision-loss RuntimeWarning is silenced on near-constant arrays
    - skew/kurtosis are 0.0 when std is near-zero (would otherwise be 0/0)
    """
    import warnings

    import numpy as np
    from scipy import stats as scs

    q1, q3 = np.quantile(a, [0.25, 0.75])
    iqr = q3 - q1
    outlier_mask = (a < q1 - 1.5 * iqr) | (a > q3 + 1.5 * iqr)

    if a.size > 2 and float(a.std(ddof=0)) > 1e-12:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            skew = float(scs.skew(a))
            kurt = float(scs.kurtosis(a)) if a.size > 3 else 0.0
    else:
        skew = 0.0
        kurt = 0.0

    stats = {
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
    bins = min(40, max(5, int(math.sqrt(a.size))))
    hist_counts, hist_edges = np.histogram(a, bins=bins)
    sample_idx = np.random.default_rng(42).choice(
        a.size, size=min(500, a.size), replace=False
    )
    extras = {
        "histogram": {"counts": hist_counts.tolist(), "edges": hist_edges.tolist()},
        "sample": a[np.sort(sample_idx)].tolist(),
    }
    return stats, extras


def _emit_common_alerts(result: ProfileResult) -> None:
    """Append alerts common to both profile paths, skipping codes already present.

    Guards: skip-if-present means callers can invoke this alongside existing
    inline emissions without dup'ing. Stats that the path didn't compute (near-
    unique columns skip duplicate_counter; JSON-blob columns skip vocab) read
    as None and fail the predicate silently.
    """
    s = result.stats
    codes = {a.code for a in result.alerts}

    def add(level: str, code: str, condition: bool, message: str) -> None:
        if code in codes or not condition:
            return
        result.alerts.append(Alert(level, code, message))
        codes.add(code)

    add("warn", "null_rate", result.null_rate > _NULL_WARN,
        f"{result.null_rate:.1%} null")

    if result.kind == "numeric":
        skew = s.get("skew")
        outlier_rate = s.get("outlier_rate")
        add("info", "high_skew", skew is not None and abs(skew) > 2,
            f"skew={skew:+.2f}" if skew is not None else "")
        add("warn", "outliers",
            outlier_rate is not None and outlier_rate > 0.05,
            f"{outlier_rate:.1%} rows beyond 1.5 IQR" if outlier_rate is not None else "")
        add("info", "constant", result.n_unique == 1, "only one distinct value")
    elif result.kind == "text":
        len_p95 = s.get("len_p95")
        dup_rate = s.get("duplicate_rate")
        add("info", "short_text", len_p95 is not None and len_p95 < 20,
            "95th-percentile length under 20 chars")
        add("warn", "duplicates",
            dup_rate is not None and dup_rate > 0.2,
            f"{dup_rate:.1%} duplicate strings" if dup_rate is not None else "")
    elif result.kind == "categorical":
        top_rate = s.get("top_rate")
        add("warn", "imbalance",
            top_rate is not None and top_rate > _CARD_WARN,
            f"top value is {top_rate:.1%} of rows" if top_rate is not None else "")


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

    result.n_unique = clean.n_unique()
    result.stats, result.extras = _numeric_stats(clean.to_numpy())

    _emit_common_alerts(result)
    return result


_VOCAB_SAMPLE_K = 20_000
_VOCAB_ROW_CHAR_CAP = 500
_VOCAB_SKIP_AVG_LEN = 2000  # skip vocab entirely on JSON-blob-like columns
_NEAR_UNIQUE_FRAC = 0.95

# --- alt-text / caption-shaped quality signals -------------------------------
# Applied to every text column where they make sense. Generic enough to light
# up any caption/description/prose corpus, but named after the alt-text
# research motivation that first demanded them.

_EMOJI_RE = (
    r"[\U0001F300-\U0001FAFF\U0001F000-\U0001F2FF☀-➿]"
)
_URL_RE = r"https?://\S+|www\.\S+"
_BOILERPLATE_PREFIXES = (
    "a photo of",
    "an image of",
    "image of",
    "photo of",
    "picture of",
    "a picture of",
    "screenshot of",
    "this is a",
    "this image",
    "alt text:",
    "alt:",
)


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

    # --- caption/description quality signals (generic + alt-text-flavoured)

    # word-count estimate (avoid regex on huge blobs; whitespace split is O(n) and fast)
    words_per_row = clean.str.split(" ").list.len()
    words_np = words_per_row.drop_nulls().cast(pl.Int64).to_numpy()

    # caption-quality signals — all vectorised, all optional in the template
    total_non_null = clean.len()
    emoji_rows = int(clean.str.contains(_EMOJI_RE).sum()) if total_non_null else 0
    url_rows = int(clean.str.contains(_URL_RE).sum()) if total_non_null else 0
    one_word_rows = int((words_per_row <= 1).sum())
    # all-caps: row is uppercase and has at least 3 chars (skip "A", "OK")
    long_enough = clean.filter(lengths >= 3)
    allcaps_rows = int(
        (long_enough == long_enough.str.to_uppercase()).sum()
    ) if long_enough.len() else 0
    # boilerplate prefix: case-insensitive starts-with any known opener
    lowered_prefix = clean.str.to_lowercase().str.slice(0, 32)
    boilerplate_rows = 0
    for prefix in _BOILERPLATE_PREFIXES:
        boilerplate_rows += int(lowered_prefix.str.starts_with(prefix).sum())

    quality = {
        "emoji_rate": emoji_rows / total_non_null if total_non_null else 0.0,
        "url_rate": url_rows / total_non_null if total_non_null else 0.0,
        "one_word_rate": one_word_rows / total_non_null if total_non_null else 0.0,
        "allcaps_rate": allcaps_rows / total_non_null if total_non_null else 0.0,
        "boilerplate_rate": boilerplate_rows / total_non_null if total_non_null else 0.0,
    }

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
        "emoji_rate": quality["emoji_rate"],
        "url_rate": quality["url_rate"],
        "one_word_rate": quality["one_word_rate"],
        "allcaps_rate": quality["allcaps_rate"],
        "boilerplate_rate": quality["boilerplate_rate"],
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
    if lang_counts and len(lang_counts) > 3:
        result.alerts.append(
            Alert("info", "multilingual", f"{len(lang_counts)} languages detected in sample")
        )
    if vocab_skipped_reason:
        result.alerts.append(Alert("info", "vocab_skipped", vocab_skipped_reason))

    if quality["one_word_rate"] > 0.25:
        result.alerts.append(
            Alert("warn", "one_word", f"{quality['one_word_rate']:.1%} rows are a single word")
        )
    if quality["allcaps_rate"] > 0.1:
        result.alerts.append(
            Alert("info", "allcaps", f"{quality['allcaps_rate']:.1%} rows are all-caps")
        )
    if quality["url_rate"] > 0.25:
        result.alerts.append(
            Alert("info", "url_heavy", f"{quality['url_rate']:.1%} rows contain a URL")
        )
    if quality["boilerplate_rate"] > 0.2:
        result.alerts.append(
            Alert(
                "warn",
                "boilerplate",
                f"{quality['boilerplate_rate']:.1%} rows start with boilerplate ('image of', …)",
            )
        )
    _emit_common_alerts(result)
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

    if cardinality and singletons / cardinality > 0.5:
        result.alerts.append(Alert("info", "long_tail", f"{singletons} singleton categories"))
    _emit_common_alerts(result)
    return result


_LANG_RESULT_META_KEY = "_engine"


def _detect_languages(column: "pl.Series") -> dict[str, int]:
    """Language detection. Uses fasttext on the full column when available
    (fast enough for 400K rows), falls back to a bounded langdetect sample."""
    import polars as pl

    clean = column.drop_nulls().cast(pl.Utf8, strict=False).drop_nulls()
    if clean.len() == 0:
        return {}

    model_path = _ensure_fasttext_lid()
    if model_path:
        try:
            return _fasttext_detect(clean, model_path)
        except Exception:
            pass  # fall through to langdetect

    # langdetect fallback: only cheap on a sample
    sample = clean.sample(n=min(clean.len(), _LANG_SAMPLE_K), seed=42) if clean.len() > _LANG_SAMPLE_K else clean
    strings = [
        s for s in sample.to_list() if isinstance(s, str) and 8 <= len(s) <= 400
    ]
    if not strings:
        return {}
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
    counts = dict(counter.most_common(30))
    counts["__engine"] = "langdetect_sample"
    return counts


def _fasttext_detect(column: "pl.Series", model_path: str) -> dict[str, int]:
    """Run fasttext lid.176 on every eligible row."""
    import os
    import sys

    import fasttext  # type: ignore

    # silence stderr noise from fasttext model load
    devnull = open(os.devnull, "w")
    _orig_stderr = sys.stderr
    try:
        sys.stderr = devnull
        model = fasttext.load_model(model_path)
    finally:
        sys.stderr = _orig_stderr
        devnull.close()

    # fasttext refuses newlines inside a doc; strip + truncate per row, then batch
    strings = column.to_list()
    cleaned: list[str] = []
    for raw in strings:
        if not isinstance(raw, str):
            continue
        s = raw.replace("\n", " ").strip()
        if 8 <= len(s) <= 500:
            cleaned.append(s[:500])
        # very short / very long: skipped — same policy as langdetect path

    if not cleaned:
        return {}

    labels, _ = model.predict(cleaned, k=1)
    counter: Counter[str] = Counter()
    for row in labels:
        if row:
            counter[row[0].replace("__label__", "")] += 1
    counts = dict(counter.most_common(30))
    counts["__engine"] = f"fasttext:{len(cleaned):,}"
    return counts


def _ensure_fasttext_lid() -> str | None:
    """Locate the lid.176 model. Accepts env var or any of several default paths."""
    import os
    from pathlib import Path

    env = os.environ.get("SATURN_FASTTEXT_LID")
    if env and Path(env).exists():
        return env
    candidates = [
        Path.cwd() / ".cache" / "saturn" / "lid.176.bin",
        Path.home() / ".cache" / "saturn" / "lid.176.bin",
        Path(__file__).resolve().parent.parent / ".cache" / "saturn" / "lid.176.bin",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
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
    result.stats, result.extras = _numeric_stats(a)

    _emit_common_alerts(result)
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

    if lang_counter and len(lang_counter) > 3:
        result.alerts.append(
            Alert("info", "multilingual", f"{len(lang_counter)} languages detected in sample")
        )
    _emit_common_alerts(result)
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

    if result.extras["singletons"] / len(counts) > 0.5:
        result.alerts.append(
            Alert("info", "long_tail", f"{result.extras['singletons']} singleton categories")
        )
    _emit_common_alerts(result)
    return result
