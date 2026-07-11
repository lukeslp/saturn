from __future__ import annotations

import subprocess
import time
import os
from pathlib import Path

import polars as pl
import pytest


def test_analyze_upload_removes_upload_tree_on_success_error_and_timeout(tmp_path, monkeypatch):
    from saturn.viewer.runner import analyze_upload

    findings = tmp_path / "findings"
    findings.mkdir()

    for index, outcome in enumerate((subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 1, "", "bad"),
                    subprocess.TimeoutExpired("saturn", 1))):
        upload = tmp_path / f"upload-{time.time_ns()}" / "data.csv"
        upload.parent.mkdir()
        upload.write_text("a\n1\n")

        def run(*args, **kwargs):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(subprocess, "run", run)
        if isinstance(outcome, BaseException) or outcome.returncode:
            with pytest.raises((subprocess.TimeoutExpired, RuntimeError)):
                analyze_upload("job", findings, upload, f"result-{index}", None)
        else:
            analyze_upload("job", findings, upload, f"result-{index}", None)
        assert not upload.parent.exists()


@pytest.mark.parametrize(
    ("upload_name", "finding_id"),
    [("data.exe", "result"), ("data.csv", "../unsafe")],
)
def test_analyze_upload_removes_upload_tree_on_validation_error(
    tmp_path, upload_name, finding_id
):
    from saturn.viewer.runner import analyze_upload

    upload = tmp_path / "upload" / upload_name
    upload.parent.mkdir()
    upload.write_text("a\n1\n")
    with pytest.raises(ValueError):
        analyze_upload("job", tmp_path / "findings", upload, finding_id, None)
    assert not upload.parent.exists()


def test_viewer_limits_override_inherited_general_limits(tmp_path, monkeypatch):
    from saturn.viewer.runner import analyze_upload

    upload = tmp_path / "upload" / "data.csv"
    upload.parent.mkdir()
    upload.write_text("a\n1\n")
    findings = tmp_path / "findings"
    findings.mkdir()
    captured = {}
    monkeypatch.setenv("SATURN_MAX_ROWS", "999")
    monkeypatch.setenv("SATURN_MAX_COLUMNS", "998")
    monkeypatch.setenv("SATURN_MAX_CELLS", "997")
    monkeypatch.setenv("SATURN_VIEWER_MAX_ROWS", "11")
    monkeypatch.setenv("SATURN_VIEWER_MAX_COLUMNS", "12")
    monkeypatch.setenv("SATURN_VIEWER_MAX_CELLS", "13")

    def run(*args, **kwargs):
        captured.update(kwargs["env"])
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    analyze_upload("job", findings, upload, "result", None)
    assert captured["SATURN_MAX_ROWS"] == "11"
    assert captured["SATURN_MAX_COLUMNS"] == "12"
    assert captured["SATURN_MAX_CELLS"] == "13"


def test_job_executor_rejects_work_beyond_active_and_queue_capacity(monkeypatch):
    import saturn.viewer.runner as runner

    gate = runner.threading.Event()
    monkeypatch.setattr(runner, "_MAX_ACTIVE_JOBS", 1)
    monkeypatch.setattr(runner, "_MAX_QUEUED_JOBS", 1)
    runner.reset_job_runtime()

    def blocked(job_id):
        gate.wait(2)

    try:
        runner.start_job("test", blocked)
        runner.start_job("test", blocked)
        with pytest.raises(runner.JobCapacityError, match="capacity"):
            runner.start_job("test", blocked)
    finally:
        gate.set()
        runner.reset_job_runtime()


def test_completed_job_store_evicts_oldest(monkeypatch):
    import saturn.viewer.runner as runner

    monkeypatch.setattr(runner, "_MAX_RETAINED_JOBS", 2)
    runner.reset_job_runtime()
    now = time.time()
    for i in range(3):
        job = runner.Job(id=str(i), kind="test", status="done", completed_at=now + i)
        runner._JOBS[job.id] = job
    runner.prune_jobs(now=now + 10)
    assert list(runner._JOBS) == ["1", "2"]


@pytest.mark.parametrize(
    ("shape", "env", "message"),
    [
        ((3, 1), {"SATURN_MAX_ROWS": "2"}, "row limit"),
        ((1, 3), {"SATURN_MAX_COLUMNS": "2"}, "column limit"),
        ((2, 2), {"SATURN_MAX_CELLS": "3"}, "cell limit"),
    ],
)
def test_file_adapter_rejects_parsed_dataset_over_configured_limits(
    tmp_path, monkeypatch, shape, env, message
):
    from saturn.ingestion import FileAdapter

    path = tmp_path / "data.csv"
    pl.DataFrame({f"c{i}": range(shape[0]) for i in range(shape[1])}).write_csv(path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message):
        FileAdapter(path).load_dataframe()


def test_janitor_removes_expired_managed_artifacts_only(tmp_path):
    import saturn.viewer.runner as runner

    uploads = tmp_path / "uploads"
    findings = tmp_path / "findings"
    uploads.mkdir()
    findings.mkdir()
    stale_upload = uploads / "owned"
    stale_upload.mkdir()
    (stale_upload / "data.csv").write_text("a\n1")
    operator_file = findings / "preexisting.json"
    operator_file.write_text("{}")
    managed_file = findings / "generated.json"
    old = time.time() - 100
    os.utime(stale_upload, (old, old))
    runner.reserve_managed_result(managed_file, created_at=old)
    managed_file.write_text("{}")
    runner.cleanup_expired(
        upload_dir=uploads,
        upload_ttl=10,
        job_ttl=10,
        result_ttl=10,
        now=time.time(),
    )
    assert not stale_upload.exists()
    assert not managed_file.exists()
    assert operator_file.exists()


def test_managed_result_ownership_survives_runtime_restart(tmp_path):
    import saturn.viewer.runner as runner

    findings = tmp_path / "findings"
    findings.mkdir()
    managed = findings / "generated.json"
    operator = findings / "operator.json"
    operator.write_text("{}")
    old = time.time() - 100
    runner.reserve_managed_result(managed, created_at=old)
    managed.write_text("{}")

    # Simulate a fresh worker: no process-local ownership state is available.
    runner.cleanup_expired(
        upload_dir=tmp_path / "uploads",
        upload_ttl=10,
        job_ttl=10,
        result_ttl=10,
        now=time.time(),
    )
    assert not managed.exists()
    assert operator.exists()


def test_managed_result_registry_is_bounded_and_never_adopts_preexisting_file(
    tmp_path, monkeypatch
):
    import json
    import saturn.viewer.runner as runner

    findings = tmp_path / "findings"
    findings.mkdir()
    operator = findings / "operator.json"
    operator.write_text("{}")
    with pytest.raises(FileExistsError):
        runner.reserve_managed_result(operator)

    monkeypatch.setattr(runner, "_MAX_MANAGED_RESULTS", 2)
    for index in range(3):
        runner.reserve_managed_result(
            findings / f"generated-{index}.json", created_at=float(index)
        )
    manifest = json.loads((findings / ".saturn-viewer-results.json").read_text())
    assert [entry["name"] for entry in manifest["results"]] == [
        "generated-1.json", "generated-2.json"
    ]
    assert operator.exists()


def test_app_runs_configurable_janitor_without_deleting_operator_findings(tmp_path, monkeypatch):
    from saturn.viewer.app import create_app

    upload_root = tmp_path / "uploads"
    stale = upload_root / "stale"
    stale.mkdir(parents=True)
    (stale / "data.csv").write_text("a\n1")
    old = time.time() - 100
    os.utime(stale, (old, old))
    operator_file = tmp_path / "operator.json"
    operator_file.write_text("{}")
    monkeypatch.setenv("SATURN_UPLOAD_DIR", str(upload_root))
    monkeypatch.setenv("SATURN_UPLOAD_TTL_SECONDS", "10")
    monkeypatch.setenv("SATURN_JANITOR_INTERVAL_SECONDS", "0")

    app = create_app(findings_dir=tmp_path, testing=True)
    app.test_client().get("/health")

    assert not stale.exists()
    assert operator_file.exists()
