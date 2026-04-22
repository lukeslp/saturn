"""Assemble profile results into an HTML report and a JSON findings sidecar."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

    def to_findings(self) -> dict[str, Any]:
        return {
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

    # language counts aggregated across text columns
    merged: dict[str, int] = {}
    for r in results:
        if r.kind == "text":
            for lang, n in r.extras.get("language_counts", {}).items():
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
    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pct"] = lambda v: f"{(v or 0):.1%}"
    env.filters["num"] = _fmt_num

    tmpl = env.get_template("report.html.j2")
    charts: dict[str, str | None] = {r.column: chart_for(r) for r in data.results}

    html = tmpl.render(
        data=data,
        charts=charts,
        version=__version__,
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path


def write_findings(data: ReportData, output_path: Path) -> Path:
    output_path.write_text(json.dumps(data.to_findings(), indent=2, default=_json_default), encoding="utf-8")
    return output_path


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
