"""Flask app factory for the saturn live viewer.

Single entry point: `create_app(findings_dir=...)`. Routes:

  GET  /                             index with drop-zone and findings list
  GET  /view/<id>                    profile or compare view
  GET  /api/findings/<id>            raw JSON passthrough
  POST /analyze                      multipart upload, triggers background job
  POST /analyze-hf                   HF repo id, triggers background job
  POST /backfill/<id>                run LLM pass against existing findings
  GET  /jobs/<job_id>                job status (HTML page, auto-refreshing)
  GET  /jobs/<job_id>.json           job status JSON (for client polling)
  GET  /health                       readiness probe
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from .loader import FindingsKind, list_findings, load_findings
from .runner import analyze_hf, analyze_upload, backfill_insights, get_job, start_job

DEFAULT_PORT = 5043
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_ALLOWED_EXT = {
    # tabular text
    ".csv", ".tsv",
    # JSON variants
    ".jsonl", ".ndjson", ".json",
    # columnar
    ".parquet", ".feather", ".arrow",
    # spreadsheets
    ".xlsx", ".xls", ".xlsb", ".ods",
    # embedded
    ".db", ".sqlite", ".sqlite3",
}
_ID_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class PrefixMiddleware:
    """Honor `X-Forwarded-Prefix` from a reverse proxy so `url_for` prepends it.

    See docs/DEPLOY.md for why Caddy's `handle_path /saturn/*` needs this.
    Only trusted when `SATURN_TRUST_FORWARDED_PREFIX=1` is set in the env.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app
        self._trust = os.environ.get("SATURN_TRUST_FORWARDED_PREFIX") == "1"

    def __call__(self, environ, start_response):
        if self._trust:
            prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "").rstrip("/")
            if prefix:
                environ["SCRIPT_NAME"] = prefix
        return self.wsgi_app(environ, start_response)


def _safe_findings_path(findings_dir: Path, id_: str) -> Path | None:
    base = findings_dir.resolve()
    candidate = (findings_dir / f"{id_}.json").resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _slug(raw: str, fallback: str = "finding") -> str:
    slug = _ID_SAFE.sub("-", raw.strip().lower()).strip("-")
    return slug or fallback


def _unique_finding_id(findings_dir: Path, base: str) -> str:
    """Append -2, -3, … if base.json already exists."""
    candidate = base
    n = 1
    while (findings_dir / f"{candidate}.json").exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def create_app(*, findings_dir: Path, testing: bool = False) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["SATURN_FINDINGS_DIR"] = Path(findings_dir)
    app.config["SATURN_UPLOAD_DIR"] = Path(
        os.environ.get("SATURN_UPLOAD_DIR", tempfile.gettempdir()) + "/saturn-uploads"
    )
    app.config["SATURN_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["SATURN_DEFAULT_LLM"] = os.environ.get("SATURN_DEFAULT_LLM", "anthropic")
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["SECRET_KEY"] = os.environ.get("SATURN_SECRET_KEY") or os.urandom(24).hex()
    app.wsgi_app = PrefixMiddleware(app.wsgi_app)

    # ---------- read-only routes --------------------------------------------

    @app.get("/")
    def index():
        docs = list_findings(app.config["SATURN_FINDINGS_DIR"])
        return render_template(
            "index.html.j2",
            docs=docs,
            default_llm=app.config["SATURN_DEFAULT_LLM"],
        )

    @app.get("/view/<id>")
    def view(id: str):
        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        doc = load_findings(path)
        view_mode = request.args.get("view", "report").lower()
        if view_mode not in {"report", "notebook"}:
            view_mode = "report"

        charts, overview_chart = {}, None
        if view_mode == "notebook" and doc.kind is FindingsKind.PROFILE:
            from ..charts import (
                chart_for,
                correlation_heatmap,
                dataset_overview_chart,
                language_chart,
            )
            from ..report import ReportData

            report = ReportData.from_findings(doc.raw)
            for result in report.results:
                try:
                    charts[result.column] = chart_for(result)
                except Exception:
                    charts[result.column] = None
            overview_chart = dataset_overview_chart(
                [(r.column, r.null_rate) for r in report.results]
            )
            lang_counts = doc.raw.get("language_counts", {})
            charts["__languages__"] = language_chart(lang_counts) if lang_counts else None
            # Correlation across numeric columns
            import numpy as np
            numeric = [r for r in report.results
                       if r.kind == "numeric" and r.extras.get("sample")]
            if len(numeric) >= 2:
                try:
                    max_len = min(len(r.extras["sample"]) for r in numeric)
                    mat = np.vstack(
                        [np.asarray(r.extras["sample"][:max_len]) for r in numeric]
                    )
                    corr = np.corrcoef(mat)
                    charts["__correlation__"] = correlation_heatmap(
                        corr.tolist(), [r.column for r in numeric]
                    )
                except Exception:
                    charts["__correlation__"] = None

        template_name = {
            ("report", "profile"): "profile.html.j2",
            ("report", "compare"): "compare.html.j2",
            ("notebook", "profile"): "notebook_profile.html.j2",
            ("notebook", "compare"): "notebook_compare.html.j2",
        }[(view_mode, doc.kind.value)]

        return render_template(
            template_name,
            doc=doc,
            default_llm=app.config["SATURN_DEFAULT_LLM"],
            view_mode=view_mode,
            charts=charts,
            overview_chart=overview_chart,
        )

    @app.get("/api/findings/<id>")
    def api_findings(id: str):
        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        return load_findings(path).raw

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "findings_dir": str(app.config["SATURN_FINDINGS_DIR"]),
            "upload_dir": str(app.config["SATURN_UPLOAD_DIR"]),
            "default_llm": app.config["SATURN_DEFAULT_LLM"],
        }

    # ---------- mutation routes ---------------------------------------------

    @app.post("/analyze")
    def analyze_file():
        file = request.files.get("file")
        if file is None or not file.filename:
            flash("Pick a file to analyze.", "error")
            return redirect(url_for("index"))

        filename = secure_filename(file.filename)
        ext = Path(filename).suffix.lower()
        if ext not in _ALLOWED_EXT:
            flash(
                f"Unsupported file type {ext!r}. Saturn reads "
                + ", ".join(sorted(_ALLOWED_EXT)) + ".",
                "error",
            )
            return redirect(url_for("index"))

        upload_subdir = app.config["SATURN_UPLOAD_DIR"] / uuid.uuid4().hex[:12]
        upload_subdir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_subdir / filename
        file.save(upload_path)

        provider = request.form.get("llm") or app.config["SATURN_DEFAULT_LLM"] or None
        if request.form.get("no_llm") == "1":
            provider = None

        base = _slug(Path(filename).stem, "upload")
        finding_id = _unique_finding_id(app.config["SATURN_FINDINGS_DIR"], base)

        job = start_job(
            "analyze-upload",
            analyze_upload,
            app.config["SATURN_FINDINGS_DIR"],
            upload_path,
            finding_id,
            provider,
        )
        return redirect(url_for("job_view", job_id=job.id))

    @app.post("/analyze-hf")
    def analyze_hf_route():
        repo = (request.form.get("repo") or "").strip()
        if not repo:
            flash("Give a HuggingFace repo id (user/dataset).", "error")
            return redirect(url_for("index"))

        provider = request.form.get("llm") or app.config["SATURN_DEFAULT_LLM"] or None
        if request.form.get("no_llm") == "1":
            provider = None

        base = _slug(repo.replace("/", "--"), "hf-finding")
        finding_id = _unique_finding_id(app.config["SATURN_FINDINGS_DIR"], base)

        try:
            job = start_job(
                "analyze-hf",
                analyze_hf,
                app.config["SATURN_FINDINGS_DIR"],
                repo,
                finding_id,
                provider,
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("index"))
        return redirect(url_for("job_view", job_id=job.id))

    @app.post("/backfill/<id>")
    def backfill(id: str):
        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        provider = request.form.get("llm") or app.config["SATURN_DEFAULT_LLM"]
        job = start_job(
            "backfill",
            backfill_insights,
            app.config["SATURN_FINDINGS_DIR"],
            id,
            provider,
        )
        return redirect(url_for("job_view", job_id=job.id))

    # ---------- job status --------------------------------------------------

    @app.get("/jobs/<job_id>")
    def job_view(job_id: str):
        job = get_job(job_id)
        if job is None:
            abort(404)
        return render_template("job.html.j2", job=job.to_dict())

    @app.get("/jobs/<job_id>.json")
    def job_json(job_id: str):
        job = get_job(job_id)
        if job is None:
            abort(404)
        return job.to_dict()

    return app
