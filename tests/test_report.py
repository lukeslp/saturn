from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from saturn.profilers import profile_columns, profile_dataframe
from saturn.report import assemble, render_html, write_compare_findings, write_findings


def test_end_to_end_report(tmp_path: Path, tiny_synthetic):
    schema = {
        "image_alt_length": "numeric",
        "alt_text": "text",
        "author_handle": "categorical",
    }
    results = profile_columns(schema, tiny_synthetic)

    data = assemble(
        source="test://tiny",
        row_count=len(tiny_synthetic),
        sampled_rows=len(tiny_synthetic),
        seed=42,
        schema=schema,
        results=results,
        mode="sample",
    )

    html_path = render_html(data, tmp_path / "report.html")
    findings_path = write_findings(data, tmp_path / "findings.json")

    html = html_path.read_text()
    assert "saturn" in html.lower()
    assert "image_alt_length" in html
    assert "alt_text" in html
    assert "author_handle" in html
    # Plotly is embedded in the standalone template
    assert "plotly" in html.lower()
    # WCAG 2.2 AA: every Plotly figure ships a <details>Show data table</details>
    # companion in the standalone export, not just the live viewer.
    n_figs = html.count("plotly-graph-div")
    n_tables = html.count("Show data table")
    assert n_figs >= 1
    assert n_tables >= n_figs, f"{n_tables} fallback tables for {n_figs} figures"

    findings = json.loads(findings_path.read_text())
    assert findings["meta"]["source"] == "test://tiny"
    assert findings["meta"]["row_count"] == len(tiny_synthetic)
    cols = {c["column"]: c for c in findings["columns"]}
    assert cols["image_alt_length"]["kind"] == "numeric"
    assert cols["alt_text"]["kind"] == "text"
    assert cols["author_handle"]["kind"] == "categorical"


def test_write_findings_rejects_non_finite_values(tmp_path: Path):
    report = assemble(
        source="test://strict-json", row_count=1, sampled_rows=1, seed=42,
        schema={"x": "numeric"},
        results=profile_columns({"x": "numeric"}, [{"x": 1.0}]),
    )
    report.results[0].stats["bad"] = float("nan")

    with pytest.raises(ValueError, match="JSON compliant"):
        write_findings(report, tmp_path / "findings.json")


def test_write_findings_replaces_existing_file_atomically(tmp_path: Path, monkeypatch):
    report = assemble(
        source="test://atomic", row_count=1, sampled_rows=1, seed=42,
        schema={"x": "numeric"},
        results=profile_columns({"x": "numeric"}, [{"x": 1.0}]),
    )
    output = tmp_path / "findings.json"
    output.write_text("old")
    replacements = []
    import saturn.report as report_module
    real_replace = report_module.os.replace

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(report_module.os, "replace", recording_replace)
    write_findings(report, output)

    assert replacements and replacements[0][1] == output
    assert replacements[0][0].parent == output.parent
    assert json.loads(output.read_text())["meta"]["source"] == "test://atomic"
    assert not replacements[0][0].exists()


def test_compare_findings_reject_non_finite_without_damaging_existing_file(tmp_path: Path):
    class BadReport:
        def to_dict(self):
            return {"score": float("inf")}

    output = tmp_path / "compare.json"
    output.write_text("preserve me")

    with pytest.raises(ValueError, match="JSON compliant"):
        write_compare_findings(BadReport(), output)

    assert output.read_text() == "preserve me"


def test_correlation_uses_pairwise_complete_source_rows_and_records_counts():
    rows = pl.DataFrame(
        {
            "x": [1.0, 2.0, None, 4.0],
            "y": [1.0, None, 3.0, 4.0],
        }
    )
    schema = {"x": "numeric", "y": "numeric"}
    results = profile_dataframe(rows, schema)

    report = assemble(
        source="test://correlation",
        row_count=4,
        sampled_rows=4,
        seed=42,
        schema=schema,
        results=results,
        correlation_frame=rows,
    )

    assert report.correlation_labels == ["x", "y"]
    assert report.correlation_matrix == [[1.0, 1.0], [1.0, 1.0]]
    assert report.correlation_pair_counts == [[3, 2], [2, 3]]


def test_to_findings_includes_insight_bundle_when_present():
    from saturn.insights import Insight, InsightBundle
    from saturn.profilers import ProfileResult
    from saturn.report import assemble

    results = [
        ProfileResult(
            column="x",
            kind="numeric",
            n=10,
            n_null=0,
            n_unique=10,
            stats={},
            extras={},
            alerts=[],
        )
    ]
    report = assemble(
        source="s",
        row_count=10,
        sampled_rows=10,
        seed=0,
        schema={"x": "numeric"},
        results=results,
        mode="full",
    )
    report.insight_bundle = InsightBundle(
        providers=["anthropic:claude-sonnet-4-6"],
        insights=[
            Insight(
                scope="dataset",
                target="__global__",
                narrative="n",
                confidence="high",
                evidence_keys=["row_count"],
                model="anthropic:claude-sonnet-4-6",
            )
        ],
        total_usage={"input_tokens": 1, "output_tokens": 2},
    )
    findings = report.to_findings()
    assert "insights" in findings
    assert findings["insights"]["providers"] == ["anthropic:claude-sonnet-4-6"]
    assert findings["insights"]["total_usage"]["input_tokens"] == 1


def test_to_findings_omits_insights_key_when_bundle_absent():
    from saturn.profilers import ProfileResult
    from saturn.report import assemble

    results = [
        ProfileResult(
            column="x",
            kind="numeric",
            n=1,
            n_null=0,
            n_unique=1,
            stats={},
            extras={},
            alerts=[],
        )
    ]
    report = assemble(
        source="s",
        row_count=1,
        sampled_rows=1,
        seed=0,
        schema={"x": "numeric"},
        results=results,
        mode="full",
    )
    findings = report.to_findings()
    assert "insights" not in findings


def test_render_html_includes_insight_narrative_when_present(tmp_path):
    from saturn.insights import Insight, InsightBundle
    from saturn.profilers import ProfileResult
    from saturn.report import assemble, render_html

    results = [
        ProfileResult(
            column="alt_text",
            kind="text",
            n=10,
            n_null=0,
            n_unique=10,
            stats={},
            extras={},
            alerts=[],
        )
    ]
    report = assemble(
        source="s",
        row_count=10,
        sampled_rows=10,
        seed=0,
        schema={"alt_text": "text"},
        results=results,
        mode="full",
    )
    report.insight_bundle = InsightBundle(
        providers=["anthropic:claude-sonnet-4-6"],
        insights=[
            Insight(
                scope="column",
                target="alt_text",
                narrative="DISTINCTIVE-NARRATIVE-STRING",
                confidence="high",
                evidence_keys=[],
                model="anthropic:claude-sonnet-4-6",
            )
        ],
    )
    out = render_html(report, tmp_path / "r.html")
    html = out.read_text()
    assert "DISTINCTIVE-NARRATIVE-STRING" in html
    assert "anthropic:claude-sonnet-4-6" in html
