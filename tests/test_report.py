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
        sample=tiny_synthetic,
        seed=42,
        schema=schema,
        results=results,
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
