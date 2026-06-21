"""Tests for go-public blockers: fastText CC-BY-SA-3.0 attribution and the
findings-before-charts emit order.

File purpose: verify (a) reports whose language counts came from fastText carry
the CC-BY-SA-3.0 attribution in the JSON sidecar while langdetect reports do
not, and (b) a chart-rendering crash never discards the deterministic findings.

I/O: builds synthetic ReportData / CompareReport objects, asserts on
to_findings()/to_dict() output and on the CLI _emit_outputs ordering.
"""

from __future__ import annotations

import json
from pathlib import Path

from saturn.cli import _emit_outputs
from saturn.compare import ColumnComparison, CompareReport, CompareSide
from saturn.profilers import Alert, ProfileResult
from saturn.report import ReportData, assemble


def _text_col(engine: str | None) -> ProfileResult:
    lang = {"en": 100}
    if engine is not None:
        lang["__engine"] = engine
    return ProfileResult(
        column="alt_text",
        kind="text",
        n=100,
        n_null=0,
        n_unique=90,
        stats={"len_mean": 50.0},
        extras={"language_counts": lang},
        alerts=[Alert("info", "multilingual", "x")],
    )


def _report(engine: str | None) -> ReportData:
    return assemble(
        source="hf://t/d", row_count=100, sampled_rows=100, seed=0,
        schema={"alt_text": "text"}, results=[_text_col(engine)], mode="full",
    )


def test_findings_carry_fasttext_attribution():
    findings = _report("fasttext:100").to_findings()
    assert "attributions" in findings
    entry = findings["attributions"][0]
    assert entry["license"] == "CC-BY-SA-3.0"
    assert "fastText lid.176" in entry["component"]


def test_langdetect_report_has_no_attribution():
    findings = _report("langdetect_sample").to_findings()
    assert "attributions" not in findings


def test_report_without_language_detection_has_no_attribution():
    findings = _report(None).to_findings()
    assert "attributions" not in findings


def test_compare_report_carries_fasttext_attribution():
    a = _text_col("fasttext:100")
    b = _text_col("langdetect_sample")
    report = CompareReport(
        a=CompareSide(label="A", source="a", row_count=100, schema={"alt_text": "text"}),
        b=CompareSide(label="B", source="b", row_count=100, schema={"alt_text": "text"}),
        columns=[ColumnComparison(column="alt_text", kind="text", a=a, b=b)],
        generated_at="2026-01-01T00:00:00+00:00",
    )
    out = report.to_dict()
    assert "attributions" in out
    assert out["attributions"][0]["license"] == "CC-BY-SA-3.0"


def test_findings_written_before_chart_crash(tmp_path: Path):
    """A chart render that raises must NOT lose the deterministic findings."""
    data = _report("fasttext:100")
    findings_path = tmp_path / "f.json"
    html_path = tmp_path / "r.html"

    def _exploding_render(_data, _out):
        raise ValueError("simulated NaN-only chart coords")

    def _real_write(d, out):
        out.write_text(json.dumps(d.to_findings()), encoding="utf-8")
        return out

    # Must not raise: render failure degrades to a warning, findings survive.
    _emit_outputs(
        data, out=html_path, findings=findings_path, open_browser=False,
        render=_exploding_render, write=_real_write,
    )
    assert findings_path.is_file()
    payload = json.loads(findings_path.read_text())
    assert payload["columns"][0]["column"] == "alt_text"
    assert not html_path.exists()  # render crashed before writing HTML
