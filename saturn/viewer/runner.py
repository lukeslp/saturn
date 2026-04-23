"""Background job runner for the viewer.

Three jobs exist: `backfill`, `analyze-upload`, `analyze-hf`. Jobs run in a
`threading.Thread` and record their status in a process-local dict. Jobs are
intentionally ephemeral: a restart loses in-flight work, and the only durable
state is the findings JSON the job writes to disk when it succeeds.

No external queue (redis/celery) because the only writer is the viewer itself
and saturn operations are bounded-duration. If that changes, swap this module
for a thin adapter over the real queue — `start_job`, `get_job` is the full
public contract.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------- job store ---------------------------------------------------------


@dataclass
class Job:
    id: str
    kind: str  # "backfill" | "analyze-upload" | "analyze-hf"
    status: str = "pending"  # "pending" | "running" | "done" | "error"
    message: str = ""
    finding_id: str | None = None  # populated when complete and navigable
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "finding_id": self.finding_id,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration": (self.completed_at - self.started_at) if self.completed_at else None,
        }


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _set(job_id: str, **kv: Any) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        for k, v in kv.items():
            setattr(job, k, v)


def get_job(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def start_job(kind: str, target: Callable[..., None], *args: Any, **kwargs: Any) -> Job:
    job = Job(id=_new_id(), kind=kind)
    with _LOCK:
        _JOBS[job.id] = job

    def _run():
        _set(job.id, status="running")
        try:
            target(job.id, *args, **kwargs)
            if get_job(job.id).status != "error":
                _set(job.id, status="done", completed_at=time.time())
        except Exception as e:
            _set(
                job.id,
                status="error",
                error=f"{type(e).__name__}: {e}",
                message="".join(traceback.format_exception_only(type(e), e)).strip(),
                completed_at=time.time(),
            )

    threading.Thread(target=_run, daemon=True, name=f"saturn-{kind}-{job.id}").start()
    return job


# ---------- job bodies --------------------------------------------------------


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_HF_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def backfill_insights(
    job_id: str,
    findings_dir: Path,
    finding_id: str,
    provider_spec: str,
) -> None:
    """Load an existing findings JSON, run the LLM pass, write it back."""
    import json

    if not _SAFE_ID.match(finding_id):
        raise ValueError(f"unsafe finding id: {finding_id!r}")

    path = findings_dir / f"{finding_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no findings file: {finding_id}")

    _set(job_id, message="loading findings")

    # Lazy imports so the viewer can boot without ~/shared on PYTHONPATH
    from ..llm.engine import run_compare_insights, run_insights
    from ..llm.gateway import parse_provider_spec
    from ..llm.keys import MissingKeyError, load_api_keys
    from ..report import ReportData

    payload = json.loads(path.read_text())

    spec = parse_provider_spec(provider_spec)
    keys = load_api_keys([spec.provider])

    if "a" in payload and "b" in payload:
        # compare findings: rehydrate CompareReport and run compare engine
        _set(job_id, message=f"running compare insight pass ({spec.label()})")
        from ..compare import ColumnComparison, CompareReport, CompareSide

        a = CompareSide(**payload["a"])
        b = CompareSide(**payload["b"])
        columns: list[ColumnComparison] = []
        for c in payload["columns"]:
            a_pr = ReportData.from_findings(
                {"meta": {}, "schema": {}, "columns": [c["a"]] if c.get("a") else []}
            ).results[:1]
            b_pr = ReportData.from_findings(
                {"meta": {}, "schema": {}, "columns": [c["b"]] if c.get("b") else []}
            ).results[:1]
            columns.append(
                ColumnComparison(
                    column=c["column"],
                    kind=c["kind"],
                    a=a_pr[0] if a_pr else None,
                    b=b_pr[0] if b_pr else None,
                    delta=c.get("delta", {}) or {},
                    notes=c.get("notes", []) or [],
                )
            )
        report = CompareReport(
            a=a, b=b, columns=columns,
            generated_at=payload.get("generated_at", ""),
        )
        bundle = run_compare_insights(report, specs=[spec], api_keys=keys)
        report.insight_bundle = bundle
        out = report.to_dict()
    else:
        _set(job_id, message=f"running insight pass ({spec.label()})")
        data = ReportData.from_findings(payload)
        bundle = run_insights(data, specs=[spec], api_keys=keys)
        data.insight_bundle = bundle
        out = data.to_findings()

    _set(job_id, message="writing findings")
    path.write_text(json.dumps(out, default=str, indent=2))
    _set(job_id, finding_id=finding_id, message=f"{len(bundle.insights)} insight(s) generated")


_UPLOAD_EXTENSIONS = {".csv", ".jsonl", ".ndjson", ".parquet", ".json", ".db", ".sqlite", ".sqlite3"}


def analyze_upload(
    job_id: str,
    findings_dir: Path,
    upload_path: Path,
    finding_id: str,
    provider_spec: str | None,
) -> None:
    """Run saturn CLI against an uploaded file. Provider spec triggers --llm."""
    if not _SAFE_ID.match(finding_id):
        raise ValueError(f"unsafe finding id: {finding_id!r}")
    if upload_path.suffix.lower() not in _UPLOAD_EXTENSIONS:
        raise ValueError(f"unsupported file type: {upload_path.suffix}")

    _set(job_id, message=f"profiling {upload_path.name}")

    findings_path = findings_dir / f"{finding_id}.json"
    html_path = findings_dir / f"{finding_id}.html"

    cmd = [
        "saturn", "analyze", str(upload_path),
        "--out", str(html_path),
        "--findings", str(findings_path),
    ]
    if provider_spec:
        cmd += ["--llm", provider_spec]

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"/home/coolhand/shared:{env.get('PYTHONPATH', '')}"
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"saturn analyze failed: {result.stderr[-500:]}")

    _set(job_id, finding_id=finding_id, message="profile complete")


def analyze_hf(
    job_id: str,
    findings_dir: Path,
    repo: str,
    finding_id: str,
    provider_spec: str | None,
) -> None:
    """Run saturn CLI against a HuggingFace repo."""
    if not _HF_REPO.match(repo):
        raise ValueError(f"unsafe hf repo id: {repo!r} (must be `user/name`)")
    if not _SAFE_ID.match(finding_id):
        raise ValueError(f"unsafe finding id: {finding_id!r}")

    _set(job_id, message=f"profiling hf://{repo}")

    findings_path = findings_dir / f"{finding_id}.json"
    html_path = findings_dir / f"{finding_id}.html"

    cmd = [
        "saturn", "huggingface", repo,
        "--out", str(html_path),
        "--findings", str(findings_path),
    ]
    if provider_spec:
        cmd += ["--llm", provider_spec]

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"/home/coolhand/shared:{env.get('PYTHONPATH', '')}"
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"saturn huggingface failed: {result.stderr[-500:]}")

    _set(job_id, finding_id=finding_id, message="profile complete")
