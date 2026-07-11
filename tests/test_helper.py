from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from saturn.cli import app


runner = CliRunner()


def _input(path: Path, rows: list[dict], format: str = "json") -> dict:
    if format == "json":
        path.write_text(json.dumps(rows), encoding="utf-8")
    else:
        pl.DataFrame(rows).write_ipc(path)
    return {"path": path.name, "format": format, "descriptor": {"source": path.name}}


def _job(tmp_path: Path, operation: str, inputs: list[dict]) -> Path:
    path = tmp_path / "job.json"
    path.write_text(json.dumps({
        "jobVersion": 1,
        "operation": operation,
        "inputs": inputs,
        "outputPath": "result.json",
        "options": {"seed": 17},
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("format,suffix", [("json", ".json"), ("arrow", ".arrow")])
def test_helper_profiles_json_and_arrow_atomically(tmp_path: Path, format: str, suffix: str):
    source = _input(tmp_path / f"rows{suffix}", [{"score": 1}, {"score": 2}], format)
    result = runner.invoke(app, ["helper", str(_job(tmp_path, "profile", [source]))])
    assert result.exit_code == 0, result.stdout
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["event"] == "complete"
    assert all(set(event) == {"event", "phase", "fraction", "message"} for event in events)
    artifact = json.loads((tmp_path / "result.json").read_text())
    assert artifact["contractVersion"] == 1
    assert artifact["kind"] == "dataset_profile"
    assert artifact["descriptor"]["source"] == f"rows{suffix}"
    assert not list(tmp_path.glob(".result.json.*.tmp"))


def test_helper_compares_two_inputs_without_coercing_schema(tmp_path: Path):
    inputs = [
        _input(tmp_path / "a.json", [{"value": 1}, {"value": 2}]),
        _input(tmp_path / "b.json", [{"value": "one"}, {"value": "two"}]),
    ]
    inputs[0]["descriptor"]["label"] = "numeric"
    inputs[1]["descriptor"]["label"] = "text"
    result = runner.invoke(app, ["helper", str(_job(tmp_path, "compare", inputs))])
    assert result.exit_code == 0, result.stdout
    artifact = json.loads((tmp_path / "result.json").read_text())
    assert artifact["kind"] == "dataset_comparison"
    assert artifact["columns"][0]["compatible"] is False
    assert artifact["columns"][0]["delta"] == {}


@pytest.mark.parametrize("mutation,code", [
    (lambda job: job.pop("jobVersion"), "invalid_envelope"),
    (lambda job: job.update(jobVersion=2), "unsupported_job_version"),
    (lambda job: job.update(outputPath="../escaped.json"), "path_outside_job_directory"),
])
def test_helper_rejects_bad_envelopes_and_traversal(tmp_path: Path, mutation, code: str):
    source = _input(tmp_path / "rows.json", [{"x": 1}])
    job_path = _job(tmp_path, "profile", [source])
    job = json.loads(job_path.read_text())
    mutation(job)
    job_path.write_text(json.dumps(job))
    result = runner.invoke(app, ["helper", str(job_path)])
    assert result.exit_code != 0
    event = json.loads(result.stdout.splitlines()[-1])
    assert event["event"] == "error"
    assert event["phase"] == code
    assert not (tmp_path.parent / "escaped.json").exists()


def test_helper_failure_preserves_existing_output_and_removes_temps(tmp_path: Path):
    (tmp_path / "bad.json").write_text("not json")
    (tmp_path / "result.json").write_text("original")
    source = {"path": "bad.json", "format": "json", "descriptor": {"source": "bad"}}
    result = runner.invoke(app, ["helper", str(_job(tmp_path, "profile", [source]))])
    assert result.exit_code != 0
    assert (tmp_path / "result.json").read_text() == "original"
    assert not list(tmp_path.glob(".*.tmp"))


def test_direct_adapter_and_helper_are_semantically_equivalent(tmp_path: Path):
    from saturn.helper import profile_artifact

    rows = [{"score": 1, "group": "a"}, {"score": 2, "group": "b"}]
    source = _input(tmp_path / "rows.json", rows)
    result = runner.invoke(app, ["helper", str(_job(tmp_path, "profile", [source]))])
    assert result.exit_code == 0, result.stdout
    via_helper = json.loads((tmp_path / "result.json").read_text())
    direct = profile_artifact(pl.DataFrame(rows), descriptor={"source": "rows.json"}, options={"seed": 17})
    via_helper["provenance"].pop("generatedAt", None)
    direct["provenance"].pop("generatedAt", None)
    assert via_helper == direct


def test_module_subprocess_emits_only_ndjson(tmp_path: Path):
    source = _input(tmp_path / "rows.json", [{"x": 1}])
    job = _job(tmp_path, "profile", [source])
    result = subprocess.run(
        [sys.executable, "-m", "saturn.cli", "helper", str(job)],
        text=True, capture_output=True, cwd=Path(__file__).parents[1], check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert all(isinstance(json.loads(line), dict) for line in result.stdout.splitlines())


def test_subprocess_sigterm_emits_cancellation_and_leaves_no_artifact(tmp_path: Path):
    rows = [{"value": index} for index in range(250_000)]
    source = _input(tmp_path / "rows.json", rows)
    job = _job(tmp_path, "profile", [source])
    process = subprocess.Popen(
        [sys.executable, "-m", "saturn.cli", "helper", str(job)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=Path(__file__).parents[1],
    )
    assert process.stdout is not None
    first = process.stdout.readline()
    assert json.loads(first)["event"] == "progress"
    process.terminate()
    stdout, stderr = process.communicate(timeout=10)
    events = [json.loads(line) for line in (first + stdout).splitlines()]
    assert process.returncode != 0
    assert events[-1]["event"] == "cancelled"
    assert stderr == ""
    assert not (tmp_path / "result.json").exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_helper_module_does_not_import_web_templates_or_provider_stack():
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('flask', 'jinja2', 'plotly', 'saturn.viewer', 'saturn.llm')):
        raise RuntimeError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import saturn.helper
"""
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True,
                            cwd=Path(__file__).parents[1], check=False)
    assert result.returncode == 0, result.stderr
