from __future__ import annotations

import json
from pathlib import Path

from saturn.profilers import profile_columns
from saturn.report import assemble, render_html, write_findings


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
    # plotly CDN injected via template
    assert "plotly" in html.lower()

    findings = json.loads(findings_path.read_text())
    assert findings["meta"]["source"] == "test://tiny"
    assert findings["meta"]["row_count"] == len(tiny_synthetic)
    cols = {c["column"]: c for c in findings["columns"]}
    assert cols["image_alt_length"]["kind"] == "numeric"
    assert cols["alt_text"]["kind"] == "text"
    assert cols["author_handle"]["kind"] == "categorical"


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
