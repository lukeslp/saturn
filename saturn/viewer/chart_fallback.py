"""Tabular fallbacks for Plotly figures.

Plotly is interactive but screen-reader-poor — its <div> wrapper exposes the
chart as a noisy "graphics-document" with no row/cell semantics. We pair every
chart with a `<details>Show data</details>` block containing the same numbers
as a proper `<table>`. Two axes met:

1. WCAG 2.2 AA — non-text content needs a text alternative.
2. nbconvert — when the notebook view ships as HTML to a non-Jupyter reader,
   the table is searchable / copyable / screenshot-friendly.

Each builder takes the raw saturn structures and returns a `dict` shape:
    {"caption": str, "headers": list[str], "rows": list[list[str|int|float]]}

The template iterates over headers/rows; numeric formatting happens in the
Jinja layer with `tabular-nums` styling already in place.
"""

from __future__ import annotations

from typing import Any


def column_data_table(column: dict[str, Any]) -> dict[str, Any] | None:
    """Build a fallback table for a per-column figure.

    Numeric → histogram (bin range, count).
    Text → length-histogram (bin range, count) when present, else top words.
    Categorical → top values (value, count, share).
    """
    kind = column.get("kind")
    extras = column.get("extras") or {}
    name = column.get("column", "?")

    if kind == "numeric":
        hist = extras.get("histogram") or {}
        counts = hist.get("counts") or []
        edges = hist.get("edges") or []
        if not counts or len(edges) < 2:
            return None
        rows = []
        for i, c in enumerate(counts):
            lo = edges[i]
            hi = edges[i + 1]
            rows.append([f"{lo:.4g} – {hi:.4g}", int(c)])
        return {
            "caption": f"Histogram bins for {name} (median: {column.get('stats', {}).get('median', '?')}).",
            "headers": ["bin", "count"],
            "rows": rows,
        }

    if kind == "categorical":
        top = extras.get("top_values") or []
        if not top:
            return None
        # Saturn stores top_values as [(value, count)] tuples, JSON-serialised
        # to lists. Compute share where the column total is available.
        n = column.get("n") or sum(int(t[1]) for t in top if len(t) >= 2)
        rows = []
        for item in top[:25]:
            if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                continue
            val, count = item[0], int(item[1])
            share = (count / n * 100) if n else 0
            rows.append([str(val), count, f"{share:.1f}%"])
        return {
            "caption": f"Top values for {name} ({len(top)} unique shown, of {column.get('n_unique', '?')} total).",
            "headers": ["value", "count", "share"],
            "rows": rows,
        }

    if kind == "text":
        # Prefer the length histogram (matches the chart_for() default for text)
        lh = extras.get("length_histogram") or {}
        counts = lh.get("counts") or []
        edges = lh.get("edges") or []
        if counts and len(edges) >= 2:
            rows = []
            for i, c in enumerate(counts):
                lo, hi = edges[i], edges[i + 1]
                rows.append([f"{lo:.0f} – {hi:.0f}", int(c)])
            return {
                "caption": f"Character-length distribution for {name} (mean: "
                           f"{column.get('stats', {}).get('len_mean', '?')}).",
                "headers": ["chars", "count"],
                "rows": rows,
            }
        # Fallback for text columns without a length histogram: top words
        top_words = extras.get("top_words") or []
        if top_words:
            rows = [[str(item[0]), int(item[1])] for item in top_words[:20]
                    if isinstance(item, (list, tuple)) and len(item) >= 2]
            return {
                "caption": f"Top words for {name}.",
                "headers": ["word", "count"],
                "rows": rows,
            }

    return None


def overview_data_table(columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-column null rate, sorted to match the bar chart order."""
    rows = []
    for c in columns:
        rate = (c.get("null_rate") or 0) * 100
        rows.append([str(c.get("column", "?")), str(c.get("kind", "?")), f"{rate:.1f}%"])
    return {
        "caption": "Per-column null rate across the corpus.",
        "headers": ["column", "kind", "null %"],
        "rows": rows,
    }


def language_data_table(language_counts: dict[str, int]) -> dict[str, Any] | None:
    """Language → count, sorted descending. Drops `__engine` provenance keys."""
    cleaned = {k: v for k, v in (language_counts or {}).items()
               if not k.startswith("__") and isinstance(v, int)}
    if not cleaned:
        return None
    total = sum(cleaned.values())
    rows = []
    for lang, n in sorted(cleaned.items(), key=lambda kv: -kv[1]):
        share = (n / total * 100) if total else 0
        rows.append([lang, n, f"{share:.1f}%"])
    return {
        "caption": f"Per-language counts (total {total:,} detected strings).",
        "headers": ["lang", "count", "share"],
        "rows": rows,
    }


def correlation_data_table(corr: list[list[float]],
                           labels: list[str]) -> dict[str, Any] | None:
    """Square correlation matrix as a table. Truncated to 12×12 for readability."""
    if not corr or not labels:
        return None
    cap = 12
    if len(labels) > cap:
        labels = labels[:cap]
        corr = [row[:cap] for row in corr[:cap]]
    headers = [""] + labels
    rows = []
    for label, row in zip(labels, corr):
        rows.append([label] + [f"{val:+.2f}" if isinstance(val, (int, float)) else str(val)
                                for val in row])
    return {
        "caption": f"Pearson correlation across {len(labels)} numeric columns "
                   f"(values clipped to 2 decimals).",
        "headers": headers,
        "rows": rows,
    }
