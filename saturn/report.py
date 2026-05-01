"""Assemble profile results into an HTML report and a JSON findings sidecar."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__
from .charts import (
    chart_for,
    correlation_heatmap,
    dataset_overview_chart,
    language_chart,
)
from .profilers import ProfileResult

if TYPE_CHECKING:
    from .insights import InsightBundle


@dataclass
class DatasetMeta:
    source: str
    row_count: int | None
    sampled_rows: int
    seed: int
    mode: str = "full"  # 'full' or 'sample'
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ReportData:
    meta: DatasetMeta
    schema: dict[str, str]
    results: list[ProfileResult]
    overview_chart_html: str | None = None
    language_chart_html: str | None = None
    correlation_chart_html: str | None = None
    language_counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    insight_bundle: "InsightBundle | None" = None

    def to_findings(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "saturn_version": __version__,
            "meta": {
                "source": self.meta.source,
                "row_count": self.meta.row_count,
                "sampled_rows": self.meta.sampled_rows,
                "seed": self.meta.seed,
                "mode": self.meta.mode,
                "generated_at": self.meta.generated_at,
            },
            "schema": self.schema,
            "language_counts": self.language_counts,
            "notes": self.notes,
            "columns": [r.to_dict() for r in self.results],
        }
        if self.insight_bundle is not None:
            out["insights"] = self.insight_bundle.to_dict()
        return out

    @classmethod
    def from_findings(cls, payload: dict[str, Any]) -> "ReportData":
        """Rebuild a ReportData from a findings JSON payload.

        Enables the backfill flow: load findings from disk, run the LLM pass
        against the same aggregates the viewer already has, write insights back.
        Charts and correlation matrix are not restored — they're expensive
        render artifacts, not contract data. The insight pass only needs
        `results` + `meta`.
        """
        from .profilers import Alert, ProfileResult

        meta_raw = payload.get("meta", {})
        meta = DatasetMeta(
            source=meta_raw.get("source", ""),
            row_count=meta_raw.get("row_count"),
            sampled_rows=meta_raw.get("sampled_rows", 0),
            seed=meta_raw.get("seed", 42),
            mode=meta_raw.get("mode", "full"),
            generated_at=meta_raw.get("generated_at", ""),
        )
        results: list[ProfileResult] = []
        for col in payload.get("columns", []):
            alerts = [
                Alert(level=a["level"], code=a["code"], message=a["message"])
                for a in col.get("alerts", [])
            ]
            results.append(
                ProfileResult(
                    column=col["column"],
                    kind=col["kind"],
                    n=col.get("n", 0),
                    n_null=col.get("n_null", 0),
                    n_unique=col.get("n_unique"),
                    stats=col.get("stats", {}) or {},
                    extras=col.get("extras", {}) or {},
                    alerts=alerts,
                )
            )
        data = cls(
            meta=meta,
            schema=payload.get("schema", {}),
            results=results,
            language_counts=payload.get("language_counts", {}) or {},
            notes=payload.get("notes", []) or [],
        )
        if "insights" in payload:
            from .insights import Critique, Insight, InsightBundle

            raw = payload["insights"]
            insights = []
            for ins in raw.get("insights", []):
                critiques = [
                    Critique(
                        reviewer_model=c["reviewer_model"],
                        verdict=c["verdict"],
                        reason=c["reason"],
                    )
                    for c in ins.get("critiques", [])
                ]
                insights.append(
                    Insight(
                        scope=ins["scope"],
                        target=ins["target"],
                        narrative=ins["narrative"],
                        confidence=ins["confidence"],
                        evidence_keys=ins.get("evidence_keys", []),
                        model=ins["model"],
                        critiques=critiques,
                        role=ins.get("role"),
                        treatment=ins.get("treatment"),
                        featured_charts=ins.get("featured_charts", []) or [],
                    )
                )
            data.insight_bundle = InsightBundle(
                providers=raw.get("providers", []),
                insights=insights,
                total_usage=raw.get("total_usage", {}),
                errors=raw.get("errors", []),
            )
        return data


def assemble(
    source: str,
    row_count: int | None,
    sampled_rows: int,
    seed: int,
    schema: dict[str, str],
    results: list[ProfileResult],
    mode: str = "full",
) -> ReportData:
    meta = DatasetMeta(
        source=source,
        row_count=row_count,
        sampled_rows=sampled_rows,
        seed=seed,
        mode=mode,
    )
    data = ReportData(meta=meta, schema=schema, results=results)

    # dataset-level null rates
    data.overview_chart_html = dataset_overview_chart(
        [(r.column, r.null_rate) for r in results]
    )

    # language counts aggregated across text columns (skip __engine metadata)
    merged: dict[str, int] = {}
    for r in results:
        if r.kind == "text":
            for lang, n in r.extras.get("language_counts", {}).items():
                if lang.startswith("__") or not isinstance(n, int):
                    continue
                merged[lang] = merged.get(lang, 0) + n
    data.language_counts = merged
    data.language_chart_html = language_chart(merged)

    # correlation across numeric columns (sample-based)
    numeric = [r for r in results if r.kind == "numeric" and r.extras.get("sample")]
    if len(numeric) >= 2:
        max_len = min(len(r.extras["sample"]) for r in numeric)
        mat = np.vstack([np.asarray(r.extras["sample"][:max_len]) for r in numeric])
        corr = np.corrcoef(mat)
        labels = [r.column for r in numeric]
        data.correlation_chart_html = correlation_heatmap(
            corr.tolist(), labels
        )

    return data


def render_html(data: ReportData, output_path: Path) -> Path:
    env = _jinja()
    tmpl = env.get_template("report.html.j2")
    charts: dict[str, str | None] = {r.column: chart_for(r) for r in data.results}

    html = tmpl.render(
        data=data,
        charts=charts,
        version=__version__,
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path


def render_compare_html(report, output_path: Path) -> Path:
    """Render a compare report (from saturn.compare.CompareReport)."""
    from .charts import overlay_histogram

    env = _jinja()
    tmpl = env.get_template("compare.html.j2")

    # per-column overlay chart where both sides have histograms
    charts: dict[str, str] = {}
    for c in report.columns:
        if c.a is None or c.b is None:
            continue
        if c.kind == "numeric":
            ha = c.a.extras.get("histogram")
            hb = c.b.extras.get("histogram")
            if ha and hb:
                fig = overlay_histogram(ha, hb, c.column, report.a.label, report.b.label)
                if fig:
                    charts[c.column] = fig
        elif c.kind == "text":
            ha = c.a.extras.get("length_histogram")
            hb = c.b.extras.get("length_histogram")
            if ha and hb:
                fig = overlay_histogram(ha, hb, f"{c.column} length", report.a.label, report.b.label)
                if fig:
                    charts[c.column] = fig

    html = tmpl.render(report=report, charts=charts, version=__version__)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def write_findings(data: ReportData, output_path: Path) -> Path:
    output_path.write_text(
        json.dumps(data.to_findings(), indent=2, default=_json_default), encoding="utf-8"
    )
    return output_path


def write_compare_findings(report, output_path: Path) -> Path:
    payload = {"saturn_version": __version__, **report.to_dict()}
    output_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    return output_path


def _jinja() -> Environment:
    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pct"] = lambda v: f"{(v or 0):.1%}"
    env.filters["num"] = _fmt_num
    env.filters["signed"] = _fmt_signed
    env.filters["delta_rows"] = _delta_rows
    env.filters["anchor"] = _anchor_index
    return env


def _anchor_index(column_name: str, columns) -> int:
    """Given a column name and the full column-comparison list, return the 1-based index."""
    for i, c in enumerate(columns, start=1):
        if c.column == column_name:
            return i
    return 1


def _fmt_signed(v: Any) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1000:
        return f"{f:+,.0f}"
    if abs(f) < 0.001 and f != 0:
        return f"{f:+.2e}"
    return f"{f:+,.3f}"


# order metrics are shown in the compare delta table, per kind
_NUMERIC_DELTA_KEYS = [
    ("n_unique", "distinct"),
    ("mean", "mean"),
    ("median", "median"),
    ("std", "std"),
    ("q1", "q1"),
    ("q3", "q3"),
    ("min", "min"),
    ("max", "max"),
    ("outlier_rate", "outlier rate"),
    ("skew", "skew"),
    ("null_rate", "null rate"),
]
_TEXT_DELTA_KEYS = [
    ("n_unique", "distinct"),
    ("len_mean", "mean length"),
    ("len_median", "median length"),
    ("len_p95", "p95 length"),
    ("word_mean", "mean words"),
    ("duplicate_rate", "duplicate rate"),
    ("vocab_size", "vocab size (top-K)"),
    ("null_rate", "null rate"),
]
_CATEGORICAL_DELTA_KEYS = [
    ("n_unique", "distinct"),
    ("entropy", "entropy"),
    ("null_rate", "null rate"),
]


_DELTA_KEYS_BY_KIND = {
    "numeric": _NUMERIC_DELTA_KEYS,
    "text": _TEXT_DELTA_KEYS,
    "categorical": _CATEGORICAL_DELTA_KEYS,
}


def _delta_rows(delta: dict[str, Any], kind: str = ""):
    """Jinja filter: yield (display_key, a_val, b_val, delta, note) tuples for `kind`."""
    keys = _DELTA_KEYS_BY_KIND.get(kind, [])

    for raw, pretty in keys:
        a_val = delta.get(f"{raw}_a")
        b_val = delta.get(f"{raw}_b")
        d_val = delta.get(f"{raw}_delta")
        if a_val is None and b_val is None:
            continue
        yield pretty, a_val, b_val, d_val, None

    for extra_key, note in (
        ("top_value_jaccard", "top-value overlap"),
        ("top_word_jaccard", "top-word overlap"),
        ("language_jaccard", "language overlap"),
    ):
        if extra_key in delta:
            yield note, None, None, delta[extra_key], "jaccard"
    if delta.get("languages_only_a"):
        yield (
            "languages only in a",
            None,
            None,
            None,
            ", ".join(delta["languages_only_a"]),
        )
    if delta.get("languages_only_b"):
        yield (
            "languages only in b",
            None,
            None,
            None,
            ", ".join(delta["languages_only_b"]),
        )


def _fmt_num(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        if abs(v) < 0.01 and v != 0:
            return f"{v:.2e}"
        return f"{v:,.3f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not serialisable: {type(o).__name__}")
