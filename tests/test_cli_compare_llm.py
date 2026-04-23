"""Tests for `saturn compare --llm`."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from saturn.cli import app
from saturn.insights import Insight, InsightBundle


def _bundle() -> InsightBundle:
    return InsightBundle(
        providers=["anthropic:claude-sonnet-4-6"],
        insights=[
            Insight(
                scope="compare",
                target="__global__",
                narrative="curated writes shorter alt text.",
                confidence="high",
                evidence_keys=["len_mean_delta"],
                model="anthropic:claude-sonnet-4-6",
            )
        ],
        total_usage={"input_tokens": 5, "output_tokens": 3},
    )


def _write_csv(path):
    path.write_text(
        "slice,alt_text\n"
        "curated,long alt text here\n"
        "curated,another long description\n"
        "curated,one more curated entry\n"
        "firehose,x\n"
        "firehose,y\n"
        "firehose,z\n"
    )


def test_compare_with_llm_flag_invokes_compare_engine(tmp_path):
    runner = CliRunner()
    csv = tmp_path / "t.csv"
    _write_csv(csv)
    findings = tmp_path / "r.json"

    with patch(
        "saturn.cli.run_compare_insights", return_value=_bundle()
    ) as mock_engine, patch(
        "saturn.cli.load_api_keys", return_value={"anthropic": "sk"}
    ):
        result = runner.invoke(
            app,
            [
                "compare",
                str(csv),
                "--by", "slice",
                "--out", str(tmp_path / "r.html"),
                "--findings", str(findings),
                "--llm", "anthropic:claude-sonnet-4-6",
            ],
        )
    assert result.exit_code == 0, result.output
    mock_engine.assert_called_once()
    text = findings.read_text()
    assert '"insights"' in text
    assert "claude-sonnet-4-6" in text


def test_compare_without_llm_omits_insights_key(tmp_path):
    runner = CliRunner()
    csv = tmp_path / "t.csv"
    _write_csv(csv)
    findings = tmp_path / "r.json"

    result = runner.invoke(
        app,
        [
            "compare",
            str(csv),
            "--by", "slice",
            "--out", str(tmp_path / "r.html"),
            "--findings", str(findings),
        ],
    )
    assert result.exit_code == 0, result.output
    assert '"insights"' not in findings.read_text()
