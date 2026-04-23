"""Tests for `saturn analyze --llm`."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from saturn.cli import app
from saturn.insights import Insight, InsightBundle


def _fake_bundle() -> InsightBundle:
    return InsightBundle(
        providers=["anthropic:claude-sonnet-4-6"],
        insights=[
            Insight(
                scope="dataset",
                target="__global__",
                narrative="n",
                confidence="high",
                evidence_keys=[],
                model="anthropic:claude-sonnet-4-6",
            )
        ],
        total_usage={"input_tokens": 5, "output_tokens": 3},
    )


def _write_csv(path):
    path.write_text("a,b\n1,x\n2,y\n3,z\n4,w\n5,v\n")


def test_analyze_with_llm_flag_invokes_engine(tmp_path):
    runner = CliRunner()
    csv = tmp_path / "t.csv"
    _write_csv(csv)
    out = tmp_path / "r.html"
    findings = tmp_path / "r.json"

    with patch("saturn.cli.run_insights", return_value=_fake_bundle()) as mock_engine, \
         patch("saturn.cli.load_api_keys", return_value={"anthropic": "sk"}):
        result = runner.invoke(
            app,
            [
                "analyze",
                str(csv),
                "--out", str(out),
                "--findings", str(findings),
                "--llm", "anthropic:claude-sonnet-4-6",
            ],
        )
    assert result.exit_code == 0, result.output
    mock_engine.assert_called_once()
    # JSON findings must now contain the insights block
    text = findings.read_text()
    assert '"insights"' in text
    assert "claude-sonnet-4-6" in text


def test_analyze_without_llm_flag_produces_no_insights_key(tmp_path):
    runner = CliRunner()
    csv = tmp_path / "t.csv"
    _write_csv(csv)
    findings = tmp_path / "r.json"

    result = runner.invoke(
        app,
        ["analyze", str(csv), "--out", str(tmp_path / "r.html"), "--findings", str(findings)],
    )
    assert result.exit_code == 0, result.output
    assert '"insights"' not in findings.read_text()


def test_analyze_llm_flag_fails_open_on_missing_key(tmp_path):
    from saturn.llm.keys import MissingKeyError

    runner = CliRunner()
    csv = tmp_path / "t.csv"
    _write_csv(csv)
    findings = tmp_path / "r.json"

    with patch(
        "saturn.cli.load_api_keys",
        side_effect=MissingKeyError("no key for openai"),
    ):
        result = runner.invoke(
            app,
            [
                "analyze", str(csv),
                "--out", str(tmp_path / "r.html"),
                "--findings", str(findings),
                "--llm", "openai:gpt-4o",
            ],
        )
    # Fail-open: saturn must still exit 0 and produce deterministic output
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output.lower()
    # Deterministic JSON still written (without insights)
    assert findings.is_file()


def test_analyze_accepts_multiple_llm_specs_for_catfish(tmp_path):
    runner = CliRunner()
    csv = tmp_path / "t.csv"
    _write_csv(csv)
    findings = tmp_path / "r.json"

    seen = {}

    def _capture(report, *, specs, api_keys):
        seen["specs"] = specs
        return _fake_bundle()

    with patch("saturn.cli.run_insights", side_effect=_capture), \
         patch("saturn.cli.load_api_keys", return_value={"anthropic": "a", "openai": "b"}):
        result = runner.invoke(
            app,
            [
                "analyze", str(csv),
                "--out", str(tmp_path / "r.html"),
                "--findings", str(findings),
                "--llm", "anthropic",
                "--llm", "openai:gpt-4o-mini",
            ],
        )
    assert result.exit_code == 0, result.output
    assert [s.provider for s in seen["specs"]] == ["anthropic", "openai"]
    assert seen["specs"][1].model == "gpt-4o-mini"
