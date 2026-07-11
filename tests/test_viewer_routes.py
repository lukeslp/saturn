"""Tests for /analyze, /analyze-hf, /backfill, /jobs routes."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from saturn.viewer.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(findings_dir=tmp_path, testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


# ---------- upload route -----------------------------------------------------


def test_analyze_upload_redirects_to_job_page(client, monkeypatch):
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS

        captured["kind"] = kind
        captured["args"] = args
        job = Job(id="jobtest", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    data = {
        "file": (io.BytesIO(b"a,b\n1,x\n"), "tiny.csv"),
    }
    resp = client.post("/analyze", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "/jobs/jobtest" in resp.headers["Location"]
    assert captured["kind"] == "analyze-upload"
    # args: findings_dir, upload_path, finding_id, provider, api_key
    findings_dir, upload_path, finding_id, provider, api_key = captured["args"]
    assert upload_path.name == "tiny.csv"
    assert finding_id == "tiny"
    assert provider == "anthropic"  # default
    assert api_key is None  # no BYOK in this test


def test_analyze_upload_rejects_bad_extension(client):
    data = {"file": (io.BytesIO(b"x"), "malware.exe")}
    resp = client.post("/analyze", data=data, content_type="multipart/form-data", follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "Unsupported file type" in body


def test_analyze_upload_empty_form(client):
    resp = client.post("/analyze", data={}, content_type="multipart/form-data", follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "Pick a file" in body


def test_analyze_upload_honors_no_llm_checkbox(client, monkeypatch):
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["args"] = args
        job = Job(id="j", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post(
        "/analyze",
        data={
            "file": (io.BytesIO(b"a,b\n1,x\n"), "t.csv"),
            "no_llm": "1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    _dir, _path, _fid, provider, _key = captured["args"]
    assert provider is None


# ---------- HF route ---------------------------------------------------------


def test_analyze_hf_happy_path(client, monkeypatch):
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["kind"] = kind
        captured["args"] = args
        job = Job(id="hfjob", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post("/analyze-hf", data={"repo": "lukeslp/bluesky-alt-text"})
    assert resp.status_code == 302
    assert captured["kind"] == "analyze-hf"
    _dir, repo, _fid, _provider, _key = captured["args"]
    assert repo == "lukeslp/bluesky-alt-text"


def test_analyze_hf_empty_repo(client):
    resp = client.post("/analyze-hf", data={"repo": ""}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "HuggingFace repo" in body


# ---------- backfill ---------------------------------------------------------


def test_backfill_404_on_missing_finding(client):
    resp = client.post("/backfill/not-a-real-finding")
    assert resp.status_code == 404


def test_backfill_happy_path(client, tmp_path, monkeypatch):
    # Write a fake findings file
    (tmp_path / "demo.json").write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0,
                         "mode": "full", "generated_at": "2026-04-23T00:00:00+00:00"},
                "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
                "columns": [{"column": "a", "kind": "numeric", "n": 1, "n_null": 0,
                             "n_unique": 1, "stats": {}, "extras": {}, "alerts": [],
                             "null_rate": 0.0}],
            }
        )
    )

    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["kind"] = kind
        captured["args"] = args
        job = Job(id="bfjob", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post("/backfill/demo")
    assert resp.status_code == 302
    assert captured["kind"] == "backfill"
    _dir, finding_id, provider, api_key = captured["args"]
    assert finding_id == "demo"
    assert provider == "anthropic"
    assert api_key is None


@pytest.mark.parametrize("persisted_metadata", [True, False])
def test_backfill_preserves_schema_drift_through_rehydrate_and_rewrite(
    tmp_path, monkeypatch, persisted_metadata
):
    import polars as pl

    from saturn.compare import compare_dataframes
    from saturn.insights import InsightBundle
    from saturn.llm.compare_evidence import compare_column_evidence
    from saturn.viewer.runner import backfill_insights

    original = compare_dataframes(
        pl.DataFrame({"value": [1, 2, 3]}),
        pl.DataFrame({"value": ["one", "two", "three"]}),
        label_a="numeric",
        label_b="text",
        source_a="test://numeric",
        source_b="test://text",
        schema_a={"value": "numeric"},
        schema_b={"value": "text"},
    ).to_dict()
    if not persisted_metadata:
        original["columns"][0].pop("kind_a")
        original["columns"][0].pop("kind_b")
        original["columns"][0].pop("compatible")
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(original))
    captured = {}

    monkeypatch.setattr(
        "saturn.llm.keys.load_api_keys", lambda providers: {"anthropic": "key"}
    )

    def fake_run(report, **kwargs):
        column = report.columns[0]
        captured["compatible"] = column.compatible
        captured["divergences"] = report.divergence_summary(k=1)
        captured["evidence"] = compare_column_evidence(
            column, a_label=report.a.label, b_label=report.b.label
        )
        return InsightBundle(providers=["anthropic"])

    monkeypatch.setattr("saturn.llm.engine.run_compare_insights", fake_run)

    backfill_insights("job", tmp_path, "drift", "anthropic")

    rewritten = json.loads(path.read_text())
    column = rewritten["columns"][0]
    assert column["kind_a"] == "numeric"
    assert column["kind_b"] == "text"
    assert column["compatible"] is False
    assert captured["compatible"] is False
    assert captured["divergences"][0]["signals"] == [
        "schema drift: numeric → text"
    ]
    assert captured["evidence"]["kind_a"] == "numeric"
    assert captured["evidence"]["kind_b"] == "text"
    assert captured["evidence"]["compatible"] is False


# ---------- job status -------------------------------------------------------


def test_job_view_renders_pending(client):
    from saturn.viewer.runner import Job, _JOBS

    job = Job(id="viewjob", kind="analyze-upload", status="running", message="profiling")
    _JOBS[job.id] = job
    resp = client.get(f"/jobs/{job.id}")
    body = resp.get_data(as_text=True)
    assert "now profiling" in body
    assert "profiling" in body
    assert "http-equiv=\"refresh\"" in body


def test_job_view_renders_done(client):
    from saturn.viewer.runner import Job, _JOBS

    job = Job(id="donejob", kind="backfill", status="done",
              message="1 insight generated", finding_id="demo",
              completed_at=time.time())
    _JOBS[job.id] = job
    resp = client.get(f"/jobs/{job.id}")
    body = resp.get_data(as_text=True)
    assert "Reading ready" in body
    assert "/view/demo" in body


def test_job_view_renders_error(client):
    from saturn.viewer.runner import Job, _JOBS

    job = Job(id="errjob", kind="backfill", status="error",
              message="RuntimeError: network down",
              error="RuntimeError: network down",
              completed_at=time.time())
    _JOBS[job.id] = job
    resp = client.get(f"/jobs/{job.id}")
    body = resp.get_data(as_text=True)
    assert "couldn't finish" in body
    assert "network down" in body


def test_job_json_endpoint(client):
    from saturn.viewer.runner import Job, _JOBS

    job = Job(id="jsjob", kind="analyze-hf", status="running", message="hi")
    _JOBS[job.id] = job
    resp = client.get(f"/jobs/{job.id}.json")
    assert resp.is_json
    data = resp.get_json()
    assert data["id"] == "jsjob"
    assert data["status"] == "running"
    assert data["kind"] == "analyze-hf"


def test_job_404(client):
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/jobs/nope.json").status_code == 404


# ---------- runner safety ----------------------------------------------------


def test_backfill_runner_rejects_path_traversal(tmp_path):
    from saturn.viewer.runner import backfill_insights

    with pytest.raises(ValueError, match="unsafe finding id"):
        backfill_insights("j", tmp_path, "../etc/passwd", "anthropic")


def test_analyze_hf_runner_rejects_bad_repo(tmp_path):
    from saturn.viewer.runner import analyze_hf

    with pytest.raises(ValueError, match="unsafe hf repo"):
        analyze_hf("j", tmp_path, "not/a/valid/repo/too-many-slashes", "fid", None)


def test_analyze_hf_runner_rejects_injection_attempt(tmp_path):
    from saturn.viewer.runner import analyze_hf

    with pytest.raises(ValueError, match="unsafe hf repo"):
        analyze_hf("j", tmp_path, "user/repo;rm -rf /", "fid", None)


# ---------- new format acceptance --------------------------------------------


@pytest.mark.parametrize("filename", [
    "sheet.xlsx", "legacy.xls", "binary.xlsb", "opendoc.ods",
    "data.tsv", "columnar.feather", "arrow.arrow",
])
def test_analyze_upload_accepts_new_formats(client, monkeypatch, filename):
    """Whitelist and accept-attr cover every format the ingestion layer handles."""
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["args"] = args
        job = Job(id="fmt", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post(
        "/analyze",
        data={"file": (io.BytesIO(b"anything"), filename)},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302, f"{filename} should have been accepted"
    _dir, upload_path, _fid, _provider, _key = captured["args"]
    assert upload_path.name == filename
