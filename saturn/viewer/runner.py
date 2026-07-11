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

import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
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
_MAX_ACTIVE_JOBS = int(os.environ.get("SATURN_MAX_ACTIVE_JOBS", "2"))
_MAX_QUEUED_JOBS = int(os.environ.get("SATURN_MAX_QUEUED_JOBS", "6"))
_MAX_RETAINED_JOBS = int(os.environ.get("SATURN_MAX_RETAINED_JOBS", "200"))
_JOB_TTL = int(os.environ.get("SATURN_JOB_TTL_SECONDS", "86400"))
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_ACTIVE_JOBS, thread_name_prefix="saturn-job")
_CAPACITY = threading.BoundedSemaphore(_MAX_ACTIVE_JOBS + _MAX_QUEUED_JOBS)
_MAX_MANAGED_RESULTS = int(os.environ.get("SATURN_MAX_MANAGED_RESULTS", "1000"))
_RESULT_MANIFEST = ".saturn-viewer-results.json"


class JobCapacityError(RuntimeError):
    """Raised when the bounded viewer worker pool and queue are full."""


def reset_job_runtime() -> None:
    """Reset process-local state. Intended for tests and orderly shutdown."""
    global _EXECUTOR, _CAPACITY
    _EXECUTOR.shutdown(wait=False, cancel_futures=True)
    with _LOCK:
        _JOBS.clear()
    _EXECUTOR = ThreadPoolExecutor(
        max_workers=_MAX_ACTIVE_JOBS, thread_name_prefix="saturn-job"
    )
    _CAPACITY = threading.BoundedSemaphore(_MAX_ACTIVE_JOBS + _MAX_QUEUED_JOBS)


def prune_jobs(*, now: float | None = None, ttl: int | None = None) -> None:
    now = time.time() if now is None else now
    ttl = _JOB_TTL if ttl is None else ttl
    with _LOCK:
        expired = [
            job_id for job_id, job in _JOBS.items()
            if job.completed_at is not None and now - job.completed_at > ttl
        ]
        for job_id in expired:
            _JOBS.pop(job_id, None)
        completed = sorted(
            (job for job in _JOBS.values() if job.completed_at is not None),
            key=lambda job: job.completed_at or 0,
        )
        for job in completed[:-_MAX_RETAINED_JOBS]:
            _JOBS.pop(job.id, None)


def _read_result_manifest(findings_dir: Path) -> list[dict[str, Any]]:
    path = findings_dir / _RESULT_MANIFEST
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    results = payload.get("results", []) if isinstance(payload, dict) else []
    return [entry for entry in results if isinstance(entry, dict)]


def _write_result_manifest(findings_dir: Path, entries: list[dict[str, Any]]) -> None:
    findings_dir.mkdir(parents=True, exist_ok=True)
    path = findings_dir / _RESULT_MANIFEST
    temp = findings_dir / f".{_RESULT_MANIFEST}.{uuid.uuid4().hex}.tmp"
    data = json.dumps({"version": 1, "results": entries}, indent=2) + "\n"
    try:
        with temp.open("w") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def reserve_managed_result(path: Path, *, created_at: float | None = None) -> None:
    """Durably reserve a new viewer-owned result before a subprocess writes it."""
    path = path.resolve()
    if path.suffix != ".json" or path.name == _RESULT_MANIFEST:
        raise ValueError(f"invalid managed result path: {path}")
    html_path = path.with_suffix(".html")
    with _LOCK:
        if path.exists() or html_path.exists():
            raise FileExistsError(f"refusing to adopt preexisting result: {path.name}")
        entries = _read_result_manifest(path.parent)
        if any(entry.get("name") == path.name for entry in entries):
            raise FileExistsError(f"result is already reserved: {path.name}")
        entries.append({
            "name": path.name,
            "created_at": time.time() if created_at is None else created_at,
        })
        entries.sort(key=lambda entry: float(entry.get("created_at", 0)))
        dropped = entries[:-_MAX_MANAGED_RESULTS]
        for entry in dropped:
            dropped_path = path.parent / entry["name"]
            dropped_path.unlink(missing_ok=True)
            dropped_path.with_suffix(".html").unlink(missing_ok=True)
        _write_result_manifest(path.parent, entries[-_MAX_MANAGED_RESULTS:])


def cleanup_expired(
    *, upload_dir: Path, upload_ttl: int, job_ttl: int, result_ttl: int,
    now: float | None = None, findings_dir: Path | None = None,
) -> None:
    """Remove stale viewer-owned artifacts without touching operator files."""
    now = time.time() if now is None else now
    if upload_dir.is_dir():
        for child in upload_dir.iterdir():
            try:
                if now - child.stat().st_mtime > upload_ttl:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            except FileNotFoundError:
                pass
    prune_jobs(now=now, ttl=job_ttl)
    findings_dirs = {findings_dir or (upload_dir.parent / "findings")}
    with _LOCK:
        for findings_dir in findings_dirs:
            manifest_path = findings_dir / _RESULT_MANIFEST
            if not manifest_path.is_file():
                continue
            entries = _read_result_manifest(findings_dir)
            retained = []
            for entry in entries:
                name = entry.get("name")
                try:
                    born = float(entry.get("created_at"))
                except (TypeError, ValueError):
                    continue
                if (
                    not isinstance(name, str)
                    or Path(name).name != name
                    or not name.endswith(".json")
                ):
                    continue
                if now - born > result_ttl:
                    path = findings_dir / name
                    path.unlink(missing_ok=True)
                    path.with_suffix(".html").unlink(missing_ok=True)
                else:
                    retained.append(entry)
            _write_result_manifest(findings_dir, retained[-_MAX_MANAGED_RESULTS:])


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
    prune_jobs()
    capacity = _CAPACITY
    executor = _EXECUTOR
    if not capacity.acquire(blocking=False):
        raise JobCapacityError("viewer job capacity is full; try again later")
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
        finally:
            capacity.release()
            prune_jobs()

    try:
        executor.submit(_run)
    except Exception:
        capacity.release()
        with _LOCK:
            _JOBS.pop(job.id, None)
        raise
    return job


# ---------- job bodies --------------------------------------------------------


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_HF_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Provider env vars we strip from a subprocess's env when the caller did not
# supply a BYOK key. Keeps the public viewer from silently using server keys.
_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "xai": "XAI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "huggingface": "HF_TOKEN",
    # Ollama is keyless against localhost; a non-"local" value here is treated
    # as a Bearer token for hosted Ollama-compatible endpoints.
    "ollama": "OLLAMA_API_KEY",
}


def backfill_insights(
    job_id: str,
    findings_dir: Path,
    finding_id: str,
    provider_spec: str,
    api_key: str | None = None,
) -> None:
    """Load an existing findings JSON, run the LLM pass, write it back.

    If `api_key` is supplied, it overrides whatever shared.config or env vars
    would have produced — this is the BYOK path. If None, fall back to the
    server's configured keys (via load_api_keys) and surface MissingKeyError
    as a job error rather than dying with an uncaught traceback.
    """
    import json

    if not _SAFE_ID.match(finding_id):
        raise ValueError(f"unsafe finding id: {finding_id!r}")

    path = findings_dir / f"{finding_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no findings file: {finding_id}")

    _set(job_id, message="loading findings")

    # Lazy imports so the viewer can boot without the optional [llm] extra.
    from ..llm.engine import run_compare_insights, run_insights
    from ..llm.gateway import parse_provider_spec
    from ..llm.keys import MissingKeyError, load_api_keys
    from ..report import ReportData, _atomic_write_text

    payload = json.loads(path.read_text())

    spec = parse_provider_spec(provider_spec)
    if api_key:
        keys = {spec.provider: api_key}
    else:
        keys = load_api_keys([spec.provider])  # raises MissingKeyError if absent

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
            kind_a = c.get("kind_a", a_pr[0].kind if a_pr else None)
            kind_b = c.get("kind_b", b_pr[0].kind if b_pr else None)
            compatible = c.get("compatible")
            if compatible is None:
                compatible = (
                    kind_a == kind_b
                    if kind_a is not None and kind_b is not None
                    else True
                )
            columns.append(
                ColumnComparison(
                    column=c["column"],
                    kind=c["kind"],
                    a=a_pr[0] if a_pr else None,
                    b=b_pr[0] if b_pr else None,
                    delta=c.get("delta", {}) or {},
                    notes=c.get("notes", []) or [],
                    kind_a=kind_a,
                    kind_b=kind_b,
                    compatible=compatible,
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
    _atomic_write_text(path, json.dumps(out, default=str, indent=2, allow_nan=False))
    _set(job_id, finding_id=finding_id, message=f"{len(bundle.insights)} insight(s) generated")


_UPLOAD_EXTENSIONS = {
    ".csv", ".tsv",
    ".jsonl", ".ndjson", ".json",
    ".parquet", ".feather", ".arrow",
    ".xlsx", ".xls", ".xlsb", ".ods",
    ".db", ".sqlite", ".sqlite3",
}


def _build_subprocess_env(provider_spec: str | None, api_key: str | None) -> dict:
    """Construct the env for `saturn` subprocess so BYOK works correctly.

    - If api_key is supplied AND a provider spec is set, inject only that key.
    - If api_key is None and SATURN_PUBLIC_KEYS=1: this is demo mode. The
      visitor's anonymous upload silently uses the server's keys
      (ConfigManager + env). That's the dr.eamer.dev/saturn posture.
    - If api_key is None and SATURN_PUBLIC_KEYS is NOT set: this is the
      private-instance posture. Keys are scrubbed and ConfigManager bypassed
      so visitors cannot accidentally drain the server's quota.
    """
    env = os.environ.copy()
    demo_mode = os.environ.get("SATURN_PUBLIC_KEYS") == "1"

    if api_key and provider_spec:
        # Only set the one key the user supplied — wipe any others to avoid
        # accidental cross-provider leaks.
        for ev in _PROVIDER_KEY_ENV.values():
            env.pop(ev, None)
        provider = provider_spec.split(":", 1)[0]
        # Ollama with the "local" sentinel needs neither OLLAMA_HOST nor
        # OLLAMA_API_KEY — the provider class falls back to localhost:11434.
        # Setting OLLAMA_API_KEY=local would make the provider send a literal
        # "Authorization: Bearer local" header, which an unauthenticated
        # localhost ollama would reject.
        if provider == "ollama" and api_key == "local":
            pass
        else:
            env_var = _PROVIDER_KEY_ENV.get(provider, f"{provider.upper()}_API_KEY")
            env[env_var] = api_key
    elif provider_spec and demo_mode:
        # Demo posture: pass through the server's keys + ConfigManager so the
        # visitor's analyze just works. Already preserved by env.copy() above.
        pass
    elif provider_spec:
        # No BYOK + provider was requested → scrub server keys (env + Config
        # Manager) so the public form can't silently drain operator quota.
        for ev in _PROVIDER_KEY_ENV.values():
            env.pop(ev, None)
        env["SATURN_LLM_DISABLE_CONFIG_MANAGER"] = "1"
    return env


def analyze_upload(
    job_id: str,
    findings_dir: Path,
    upload_path: Path,
    finding_id: str,
    provider_spec: str | None,
    api_key: str | None = None,
) -> None:
    """Run saturn CLI against an uploaded file. Provider spec triggers --llm."""
    try:
        if not _SAFE_ID.match(finding_id):
            raise ValueError(f"unsafe finding id: {finding_id!r}")
        if upload_path.suffix.lower() not in _UPLOAD_EXTENSIONS:
            raise ValueError(f"unsupported file type: {upload_path.suffix}")
        _set(job_id, message=f"profiling {upload_path.name}")
        findings_path = findings_dir / f"{finding_id}.json"
        html_path = findings_dir / f"{finding_id}.html"
        reserve_managed_result(findings_path)
        cmd = [
            "saturn", "analyze", str(upload_path),
            "--out", str(html_path),
            "--findings", str(findings_path),
        ]
        if provider_spec:
            cmd += ["--llm", provider_spec]
        env = _build_subprocess_env(provider_spec, api_key)
        env["SATURN_MAX_ROWS"] = os.environ.get("SATURN_VIEWER_MAX_ROWS", "1000000")
        env["SATURN_MAX_COLUMNS"] = os.environ.get("SATURN_VIEWER_MAX_COLUMNS", "500")
        env["SATURN_MAX_CELLS"] = os.environ.get("SATURN_VIEWER_MAX_CELLS", "50000000")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"saturn analyze failed: {result.stderr[-500:]}")
        _set(job_id, finding_id=finding_id, message="profile complete")
    finally:
        shutil.rmtree(upload_path.parent, ignore_errors=True)


def analyze_hf(
    job_id: str,
    findings_dir: Path,
    repo: str,
    finding_id: str,
    provider_spec: str | None,
    api_key: str | None = None,
) -> None:
    """Run saturn CLI against a HuggingFace repo."""
    if not _HF_REPO.match(repo):
        raise ValueError(f"unsafe hf repo id: {repo!r} (must be `user/name`)")
    if not _SAFE_ID.match(finding_id):
        raise ValueError(f"unsafe finding id: {finding_id!r}")

    _set(job_id, message=f"profiling hf://{repo}")

    findings_path = findings_dir / f"{finding_id}.json"
    html_path = findings_dir / f"{finding_id}.html"
    reserve_managed_result(findings_path)

    cmd = [
        "saturn", "huggingface", repo,
        "--out", str(html_path),
        "--findings", str(findings_path),
    ]
    if provider_spec:
        cmd += ["--llm", provider_spec]

    env = _build_subprocess_env(provider_spec, api_key)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"saturn huggingface failed: {result.stderr[-500:]}")

    _set(job_id, finding_id=finding_id, message="profile complete")
