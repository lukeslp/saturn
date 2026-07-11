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
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from saturn import __version__

from .loader import FindingsKind, list_findings, load_findings
from .runner import (
    JobCapacityError,
    analyze_hf,
    analyze_upload,
    backfill_insights,
    cleanup_expired,
    get_job,
    start_job,
)

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


def _featured_columns(doc) -> list[str]:
    """Return the column names the LLM picked as featured charts, if any."""
    insights = (doc.raw.get("insights") or {}).get("insights") or []
    for ins in insights:
        if ins.get("scope") == "dataset" and ins.get("target") == "__global__":
            fc = ins.get("featured_charts") or []
            return [item["column"] for item in fc if isinstance(item, dict) and "column" in item]
    return []


def _resolve_llm_request(app, form, *, allow_no_llm: bool = True) -> tuple[str | None, str | None]:
    """Pick (provider_spec, api_key) for a request, honoring BYOK.

    Order of precedence:
    - If the form ticked `no_llm=1` (and allow_no_llm), return (None, None).
    - If the form supplied an `api_key`, use it with the requested or default
      provider. The server's keys are NOT consulted.
    - Otherwise fall through to whatever provider+key resolution the runner
      does (config manager, env vars). If no key is reachable the LLM pass
      will fail open and the deterministic stats still write.

    Ollama is a special case: it can run keyless against a localhost endpoint.
    When provider == "ollama" and api_key is empty, we substitute the literal
    sentinel "local" so the gateway's MissingKeyError check passes and the
    OllamaProvider falls through to its OLLAMA_HOST default.

    Returning (None, None) means "skip the LLM pass entirely."
    """
    if allow_no_llm and form.get("no_llm") == "1":
        return None, None
    provider = (form.get("llm") or app.config["SATURN_DEFAULT_LLM"] or "").strip() or None
    api_key = (form.get("api_key") or "").strip() or None
    if api_key and not provider:
        # User pasted a key but no provider — assume the most-common case.
        provider = "anthropic"
    # Ollama can run with no real key; gateway just needs *something* truthy
    # to avoid MissingKeyError. The provider class itself will pick the host.
    if provider and provider.split(":", 1)[0] == "ollama" and not api_key:
        api_key = "local"
    return provider, api_key


def create_app(*, findings_dir: Path, testing: bool = False) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.jinja_env.globals["saturn_version"] = __version__
    app.config["SATURN_FINDINGS_DIR"] = Path(findings_dir)
    app.config["SATURN_UPLOAD_DIR"] = Path(
        os.environ.get("SATURN_UPLOAD_DIR", str(Path(tempfile.gettempdir()) / "saturn-uploads"))
    )
    app.config["SATURN_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["SATURN_DEFAULT_LLM"] = os.environ.get("SATURN_DEFAULT_LLM", "anthropic")
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["SECRET_KEY"] = os.environ.get("SATURN_SECRET_KEY") or os.urandom(24).hex()
    app.wsgi_app = PrefixMiddleware(app.wsgi_app)
    janitor_interval = int(os.environ.get("SATURN_JANITOR_INTERVAL_SECONDS", "300"))
    janitor_last_run = 0.0

    @app.before_request
    def run_janitor_if_due():
        nonlocal janitor_last_run
        now = time.time()
        if now - janitor_last_run >= janitor_interval:
            cleanup_expired(
                upload_dir=app.config["SATURN_UPLOAD_DIR"],
                upload_ttl=int(os.environ.get("SATURN_UPLOAD_TTL_SECONDS", "3600")),
                job_ttl=int(os.environ.get("SATURN_JOB_TTL_SECONDS", "86400")),
                result_ttl=int(os.environ.get("SATURN_RESULT_TTL_SECONDS", "86400")),
                now=now,
                findings_dir=app.config["SATURN_FINDINGS_DIR"],
            )
            janitor_last_run = now

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

        # Per-finding annotations (sidecar <id>.notes.md if present)
        from .notes import notes_path_for, render_notes
        notes_html = render_notes(notes_path_for(app.config["SATURN_FINDINGS_DIR"], id))

        charts, overview_chart = {}, None
        chart_tables: dict[str, Any] = {}
        overview_table = None
        # Notebook view always builds the full chart set; report view only
        # builds the columns the model picked as featured (cheap render).
        needs_charts = view_mode == "notebook" or _featured_columns(doc)
        if needs_charts and doc.kind is FindingsKind.PROFILE:
            from ..charts import (
                chart_for,
                correlation_heatmap,
                dataset_overview_chart,
                language_chart,
            )
            from ..report import ReportData
            from .chart_fallback import (
                column_data_table,
                correlation_data_table,
                language_data_table,
                overview_data_table,
            )

            report = ReportData.from_findings(doc.raw)
            wanted = (
                set(r.column for r in report.results)
                if view_mode == "notebook"
                else set(_featured_columns(doc))
            )
            # Map column name -> its raw findings dict so the fallback builder
            # can read extras directly (we already have them on disk).
            raw_columns_by_name = {c.get("column"): c for c in (doc.raw.get("columns") or [])}
            for result in report.results:
                if result.column not in wanted:
                    continue
                try:
                    charts[result.column] = chart_for(result)
                except Exception:
                    charts[result.column] = None
                raw_col = raw_columns_by_name.get(result.column)
                if raw_col is not None:
                    chart_tables[result.column] = column_data_table(raw_col)
            if view_mode != "notebook":
                # report view doesn't render the dataset-level charts inline
                return render_template(
                    "profile.html.j2",
                    doc=doc,
                    default_llm=app.config["SATURN_DEFAULT_LLM"],
                    view_mode=view_mode,
                    charts=charts,
                    chart_tables=chart_tables,
                    overview_chart=None,
                    overview_table=None,
                    notes_html=notes_html,
                )
            overview_chart = dataset_overview_chart(
                [(r.column, r.null_rate) for r in report.results]
            )
            overview_table = overview_data_table(doc.raw.get("columns") or [])
            lang_counts = doc.raw.get("language_counts", {})
            charts["__languages__"] = language_chart(lang_counts) if lang_counts else None
            chart_tables["__languages__"] = language_data_table(lang_counts) if lang_counts else None
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
                    labels = [r.column for r in numeric]
                    charts["__correlation__"] = correlation_heatmap(corr.tolist(), labels)
                    chart_tables["__correlation__"] = correlation_data_table(corr.tolist(), labels)
                except Exception:
                    charts["__correlation__"] = None
                    chart_tables["__correlation__"] = None

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
            chart_tables=chart_tables,
            overview_chart=overview_chart,
            overview_table=overview_table,
            notes_html=notes_html,
        )

    @app.get("/api/findings/<id>")
    def api_findings(id: str):
        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        doc = load_findings(path)
        return doc.artifact if doc.artifact is not None else doc.raw

    @app.get("/view/<id>.ipynb")
    def view_ipynb(id: str):
        from flask import Response
        from .ipynb import to_ipynb
        import json as _json

        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        doc = load_findings(path)
        notebook = to_ipynb(doc)
        body = _json.dumps(notebook, indent=1)
        resp = Response(body, mimetype="application/x-ipynb+json")
        resp.headers["Content-Disposition"] = f'attachment; filename="{id}.ipynb"'
        return resp

    @app.get("/view/<id>.html")
    def view_static_html(id: str):
        """Self-contained static HTML report written by `saturn analyze`.

        No external JS/CSS, all charts inlined. Suitable for sharing or
        embedding without a server. Falls back to 404 if the static file
        wasn't generated alongside the JSON.
        """
        from flask import send_file

        json_path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if json_path is None or not json_path.is_file():
            abort(404)
        html_path = json_path.with_suffix(".html")
        if not html_path.is_file():
            abort(404)
        return send_file(html_path, mimetype="text/html",
                         as_attachment=False, download_name=f"{id}.html")

    @app.get("/styleguide")
    def styleguide():
        return render_template("styleguide.html.j2")

    @app.get("/batch")
    def batch_status():
        """Live progress for /tmp/saturn-batch.log (the bulk run)."""
        log_path = Path(os.environ.get("SATURN_BATCH_LOG", "/tmp/saturn-batch.log"))
        lines: list[str] = []
        if log_path.is_file():
            try:
                lines = log_path.read_text().splitlines()
            except OSError:
                lines = []

        total = ok = analyze_failed = stats_only = 0
        recent: list[str] = []
        last_done = None
        for line in lines:
            if "=== batch start:" in line:
                m = re.search(r"(\d+) candidates", line)
                if m:
                    total = int(m.group(1))
            elif "✓ done" in line:
                ok += 1
                last_done = line
            elif "✓ stats only" in line:
                ok += 1
                stats_only += 1
            elif "✗ analyze failed" in line:
                analyze_failed += 1
        # last 12 events for the live tail
        for line in lines[-200:]:
            if any(marker in line for marker in ("✓ done", "✓ stats only", "✗ analyze failed", "=== batch")):
                recent.append(line)
        recent = recent[-12:]
        running = total > 0 and (ok + analyze_failed) < total

        return render_template(
            "batch.html.j2",
            total=total,
            done=ok,
            analyze_failed=analyze_failed,
            stats_only=stats_only,
            recent=recent,
            running=running,
            log_path=str(log_path),
        )

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

        provider, api_key = _resolve_llm_request(app, request.form)
        base = _slug(Path(filename).stem, "upload")
        finding_id = _unique_finding_id(app.config["SATURN_FINDINGS_DIR"], base)

        try:
            job = start_job(
                "analyze-upload",
                analyze_upload,
                app.config["SATURN_FINDINGS_DIR"],
                upload_path,
                finding_id,
                provider,
                api_key,
            )
        except JobCapacityError as e:
            import shutil
            shutil.rmtree(upload_subdir, ignore_errors=True)
            flash(str(e), "error")
            return redirect(url_for("index"))
        return redirect(url_for("job_view", job_id=job.id))

    @app.post("/analyze-hf")
    def analyze_hf_route():
        repo = (request.form.get("repo") or "").strip()
        if not repo:
            flash("Give a HuggingFace repo id (user/dataset).", "error")
            return redirect(url_for("index"))

        provider, api_key = _resolve_llm_request(app, request.form)
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
                api_key,
            )
        except (ValueError, JobCapacityError) as e:
            flash(str(e), "error")
            return redirect(url_for("index"))
        return redirect(url_for("job_view", job_id=job.id))

    @app.post("/backfill/<id>")
    def backfill(id: str):
        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        provider, api_key = _resolve_llm_request(app, request.form, allow_no_llm=False)
        if not provider:
            flash("Pick a provider (or supply an API key) to generate a summary.", "error")
            return redirect(url_for("view", id=id))
        try:
            job = start_job(
                "backfill",
                backfill_insights,
                app.config["SATURN_FINDINGS_DIR"],
                id,
                provider,
                api_key,
            )
        except JobCapacityError as e:
            flash(str(e), "error")
            return redirect(url_for("view", id=id))
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
