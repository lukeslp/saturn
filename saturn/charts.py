"""Plotly figure builders for each ProfileResult kind.

Every builder returns an HTML snippet (div + inline script). The report
renderer concatenates these into a single self-contained file that pulls
plotly.js from a CDN once in the template head.
"""

from __future__ import annotations

from typing import Any

from .profilers import ProfileResult


_LAYOUT = {
    "margin": dict(l=40, r=20, t=40, b=40),
    "font": dict(family="ui-sans-serif, system-ui, -apple-system, sans-serif", size=13),
    "paper_bgcolor": "white",
    "plot_bgcolor": "#f7f7f9",
    "colorway": ["#2d5cf6", "#8b5cf6", "#ef4444", "#10b981", "#f59e0b", "#0ea5e9"],
}


def _to_html(fig) -> str:
    return fig.to_html(include_plotlyjs=False, full_html=False, config={"displaylogo": False})


def chart_for(result: ProfileResult) -> str | None:
    if result.kind == "numeric":
        return _numeric_chart(result)
    if result.kind == "text":
        return _text_chart(result)
    if result.kind == "categorical":
        return _categorical_chart(result)
    return None


def _numeric_chart(r: ProfileResult) -> str | None:
    import plotly.graph_objects as go

    hist = r.extras.get("histogram")
    if not hist or not hist["counts"]:
        return None
    edges = hist["edges"]
    counts = hist["counts"]
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
    fig = go.Figure()
    fig.add_bar(x=centers, y=counts, marker_color=_LAYOUT["colorway"][0], name="count")
    fig.add_vline(x=r.stats["median"], line_dash="dash", line_color="#111", annotation_text="median")
    fig.update_layout(
        title=f"{r.column} distribution",
        xaxis_title=r.column,
        yaxis_title="rows",
        **_LAYOUT,
    )
    return _to_html(fig)


def _text_chart(r: ProfileResult) -> str | None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    lh = r.extras.get("length_histogram")
    wh = r.extras.get("word_histogram")
    if not lh or not lh["counts"]:
        return None

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("character length", "word count"),
    )
    for i, hist in enumerate([lh, wh], start=1):
        edges = hist["edges"]
        counts = hist["counts"]
        if not counts:
            continue
        centers = [(edges[j] + edges[j + 1]) / 2 for j in range(len(counts))]
        fig.add_trace(
            go.Bar(x=centers, y=counts, marker_color=_LAYOUT["colorway"][i - 1], showlegend=False),
            row=1,
            col=i,
        )
    fig.update_layout(title=f"{r.column} text stats", **_LAYOUT)
    return _to_html(fig)


def _categorical_chart(r: ProfileResult) -> str | None:
    import plotly.graph_objects as go

    top: list[tuple[str, int]] = r.extras.get("top_values", [])
    if not top:
        return None
    labels = [str(k) for k, _ in top]
    values = [v for _, v in top]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=_LAYOUT["colorway"][0],
        )
    )
    fig.update_layout(
        title=f"{r.column} — top {len(top)} values",
        xaxis_title="count",
        yaxis=dict(autorange="reversed"),
        **_LAYOUT,
    )
    return _to_html(fig)


def dataset_overview_chart(per_column_null_rates: list[tuple[str, float]]) -> str:
    import plotly.graph_objects as go

    per_column_null_rates = sorted(per_column_null_rates, key=lambda kv: kv[1], reverse=True)
    fig = go.Figure(
        go.Bar(
            x=[c for c, _ in per_column_null_rates],
            y=[rate for _, rate in per_column_null_rates],
            marker_color=_LAYOUT["colorway"][2],
        )
    )
    fig.update_layout(
        title="Null rate by column",
        xaxis_title="column",
        yaxis_title="null rate",
        yaxis=dict(tickformat=".0%"),
        **_LAYOUT,
    )
    return _to_html(fig)


def language_chart(language_counts: dict[str, int]) -> str | None:
    import plotly.graph_objects as go

    if not language_counts:
        return None
    labels = list(language_counts.keys())
    values = list(language_counts.values())
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.4))
    fig.update_layout(title="Language breakdown (sampled)", **_LAYOUT)
    return _to_html(fig)


def correlation_heatmap(corr: list[list[Any]], labels: list[str]) -> str | None:
    import plotly.graph_objects as go

    if not corr:
        return None
    fig = go.Figure(
        go.Heatmap(
            z=corr,
            x=labels,
            y=labels,
            colorscale="RdBu",
            zmid=0,
            zmin=-1,
            zmax=1,
        )
    )
    fig.update_layout(title="Numeric correlation (Pearson, sampled)", **_LAYOUT)
    return _to_html(fig)
