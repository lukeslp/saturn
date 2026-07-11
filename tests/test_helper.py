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


def _job(tmp_path: Path, operation: str, inputs: list[dict], *, options=None) -> Path:
    path = tmp_path / "job.json"
    path.write_text(json.dumps({
        "jobVersion": 1,
        "operation": operation,
        "inputs": inputs,
        "outputPath": "result.json",
        "options": {"seed": 17} if options is None else options,
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


@pytest.mark.parametrize("options", [
    {"seed": "17"}, {"seed": True}, {"seed": -1}, {"seed": 17, "future": True},
])
def test_helper_rejects_unsupported_or_ill_typed_options_without_output(
    tmp_path: Path, options: dict,
):
    source = _input(tmp_path / "rows.json", [{"x": 1}])
    result = runner.invoke(app, ["helper", str(_job(tmp_path, "profile", [source], options=options))])
    assert result.exit_code != 0
    event = json.loads(result.stdout.splitlines()[-1])
    assert event["event"] == "error"
    assert event["phase"] == "invalid_options"
    assert not (tmp_path / "result.json").exists()


def test_helper_failure_preserves_existing_output_and_removes_temps(tmp_path: Path):
    (tmp_path / "bad.json").write_text("not json")
    (tmp_path / "result.json").write_text("original")
    source = {"path": "bad.json", "format": "json", "descriptor": {"source": "bad"}}
    result = runner.invoke(app, ["helper", str(_job(tmp_path, "profile", [source]))])
    assert result.exit_code != 0
    assert (tmp_path / "result.json").read_text() == "original"
    assert not list(tmp_path.glob(".*.tmp"))


def _without_runtime_identifiers(artifact: dict) -> dict:
    artifact = json.loads(json.dumps(artifact))
    artifact["provenance"].pop("generatedAt", None)
    if artifact["kind"] == "dataset_profile":
        artifact["descriptor"]["source"] = "<source>"
    else:
        for side in artifact["sides"].values():
            side["source"] = "<source>"
    return artifact


def test_profile_helper_matches_independently_migrated_cli_findings(tmp_path: Path):
    from saturn.core import migrate_legacy

    rows = [{"score": 1, "group": "a"}, {"score": 2, "group": "b"}]
    source_path = tmp_path / "rows.json"
    source = _input(source_path, rows)
    legacy_path = tmp_path / "legacy-profile.json"
    cli_result = runner.invoke(app, ["analyze", str(source_path), "--seed", "17",
                                     "--findings", str(legacy_path),
                                     "--out", str(tmp_path / "profile.html")])
    assert cli_result.exit_code == 0, cli_result.stdout
    expected = migrate_legacy(json.loads(legacy_path.read_text()))

    helper_result = runner.invoke(app, ["helper", str(_job(tmp_path, "profile", [source]))])
    assert helper_result.exit_code == 0, helper_result.stdout
    actual = json.loads((tmp_path / "result.json").read_text())
    assert _without_runtime_identifiers(actual) == _without_runtime_identifiers(expected)


def test_compare_helper_matches_independently_migrated_cli_findings(tmp_path: Path):
    from saturn.core import migrate_legacy

    left_path, right_path = tmp_path / "left.json", tmp_path / "right.json"
    inputs = [_input(left_path, [{"score": 1}, {"score": 3}]),
              _input(right_path, [{"score": 2}, {"score": 8}])]
    inputs[0]["descriptor"]["label"] = "left"
    inputs[1]["descriptor"]["label"] = "right"
    legacy_path = tmp_path / "legacy-compare.json"
    cli_result = runner.invoke(app, ["compare", str(left_path), str(right_path),
                                     "--label-a", "left", "--label-b", "right",
                                     "--seed", "17", "--findings", str(legacy_path),
                                     "--out", str(tmp_path / "compare.html")])
    assert cli_result.exit_code == 0, cli_result.stdout
    expected = migrate_legacy({"saturn_version": __import__("saturn").__version__,
                               **json.loads(legacy_path.read_text())})

    helper_result = runner.invoke(app, ["helper", str(_job(tmp_path, "compare", inputs))])
    assert helper_result.exit_code == 0, helper_result.stdout
    actual = json.loads((tmp_path / "result.json").read_text())
    assert _without_runtime_identifiers(actual) == _without_runtime_identifiers(expected)


def test_viewer_loader_consumes_same_legacy_profile_artifact(tmp_path: Path):
    from saturn.viewer.loader import FindingsKind, load_findings

    source_path = tmp_path / "rows.json"
    _input(source_path, [{"score": 1}, {"score": 2}])
    legacy_path = tmp_path / "legacy.json"
    result = runner.invoke(app, ["analyze", str(source_path), "--findings", str(legacy_path),
                                 "--out", str(tmp_path / "profile.html")])
    assert result.exit_code == 0, result.stdout
    document = load_findings(legacy_path)
    assert document.kind is FindingsKind.PROFILE
    assert document.raw == json.loads(legacy_path.read_text())


@pytest.mark.parametrize("operation", ["profile", "compare"])
def test_helper_contract_artifacts_load_render_and_remain_raw_via_api(
    tmp_path: Path, operation: str,
):
    from saturn.viewer.app import create_app
    from saturn.viewer.loader import FindingsKind, load_findings

    inputs = [_input(tmp_path / "left.json", [{"score": 1}, {"score": 2}])]
    if operation == "compare":
        inputs.append(_input(tmp_path / "right.json", [{"score": 2}, {"score": 4}]))
        inputs[0]["descriptor"]["label"] = "left"
        inputs[1]["descriptor"]["label"] = "right"
    result = runner.invoke(app, ["helper", str(_job(tmp_path, operation, inputs))])
    assert result.exit_code == 0, result.stdout

    artifact_path = tmp_path / "result.json"
    artifact = json.loads(artifact_path.read_text())
    document = load_findings(artifact_path)
    expected_kind = FindingsKind.PROFILE if operation == "profile" else FindingsKind.COMPARE
    assert document.kind is expected_kind

    client = create_app(findings_dir=tmp_path, testing=True).test_client()
    assert client.get("/").status_code == 200
    view = client.get("/view/result")
    assert view.status_code == 200
    assert "score" in view.get_data(as_text=True)
    api = client.get("/api/findings/result")
    assert api.status_code == 200
    assert api.get_json() == artifact


@pytest.mark.parametrize("job_arg,code", [
    ("missing/job.json", "job_not_found"),
    (".", "job_unreadable"),
])
def test_entrypoint_job_resolution_errors_are_single_structured_events(
    tmp_path: Path, job_arg: str, code: str,
):
    result = subprocess.run(
        [sys.executable, "-m", "saturn.entrypoint", "helper", job_arg],
        text=True, capture_output=True, cwd=Path(__file__).parents[1], check=False,
    )
    assert result.returncode != 0
    assert result.stderr == ""
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert events[0]["phase"] == code


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


def test_console_entrypoint_dispatches_helper_before_importing_cli_stack(tmp_path: Path):
    source = _input(tmp_path / "rows.json", [{"x": 1}])
    job = _job(tmp_path, "profile", [source])
    code = """
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('typer', 'rich', 'flask', 'jinja2', 'plotly',
                        'saturn.cli', 'saturn.viewer', 'saturn.llm')):
        raise RuntimeError('forbidden eager import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from saturn.entrypoint import main
raise SystemExit(main(['helper', sys.argv[1]]))
"""
    result = subprocess.run([sys.executable, "-c", code, str(job)], text=True,
                            capture_output=True, cwd=Path(__file__).parents[1], check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["event"] == "complete"


def test_module_entrypoint_preserves_non_helper_commands():
    result = subprocess.run([sys.executable, "-m", "saturn.entrypoint", "version"],
                            text=True, capture_output=True, cwd=Path(__file__).parents[1],
                            check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("saturn ")
