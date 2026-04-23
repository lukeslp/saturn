"""Flask app factory for the saturn live viewer.

Single entry point: `create_app(findings_dir=...)`. The factory pattern keeps
the app construction testable (`testing=True`) and lets callers (CLI, gunicorn
in prod) configure different findings directories without module-level state.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, render_template

from .loader import FindingsKind, list_findings, load_findings

DEFAULT_PORT = 5043


def _safe_findings_path(findings_dir: Path, id_: str) -> Path | None:
    """Resolve findings_dir/<id>.json and reject anything outside findings_dir.

    Flask's default <string> converter already forbids '/' in path parameters,
    which rules out `../` traversal. This is belt-and-suspenders for the case
    where a dev switches to a different converter, or for unicode shenanigans
    that slip past url decode. Keeping the guard cheap: one resolve() each.
    """
    base = findings_dir.resolve()
    candidate = (findings_dir / f"{id_}.json").resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def create_app(*, findings_dir: Path, testing: bool = False) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["SATURN_FINDINGS_DIR"] = Path(findings_dir)
    app.config["TESTING"] = testing

    @app.get("/")
    def index():
        docs = list_findings(app.config["SATURN_FINDINGS_DIR"])
        return render_template("index.html.j2", docs=docs)

    @app.get("/view/<id>")
    def view(id: str):
        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        doc = load_findings(path)
        template = "profile.html.j2" if doc.kind is FindingsKind.PROFILE else "compare.html.j2"
        return render_template(template, doc=doc)

    @app.get("/api/findings/<id>")
    def api_findings(id: str):
        path = _safe_findings_path(app.config["SATURN_FINDINGS_DIR"], id)
        if path is None or not path.is_file():
            abort(404)
        return load_findings(path).raw

    @app.get("/health")
    def health():
        return {"status": "ok", "findings_dir": str(app.config["SATURN_FINDINGS_DIR"])}

    return app
