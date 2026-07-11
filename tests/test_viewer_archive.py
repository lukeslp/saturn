"""Regression coverage for safely serving pre-contract Saturn artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path

from saturn.viewer.app import create_app


def _client(tmp_path: Path):
    findings = tmp_path / "findings"
    archive = tmp_path / "archive"
    findings.mkdir()
    archive.mkdir()
    return create_app(
        findings_dir=findings,
        legacy_archive_dir=archive,
        testing=True,
    ).test_client(), archive


def test_index_lists_only_complete_safe_archive_entries(tmp_path):
    client, archive = _client(tmp_path)
    (archive / "historic-report.html").write_text("<h1>Historic report</h1>")
    (archive / "historic-report.ipynb").write_text(json.dumps({"cells": []}))
    (archive / "notebook-only.ipynb").write_text(json.dumps({"cells": []}))
    (archive / ".cache-historic.html").write_text("cached")
    (archive / ".cache-historic.ipynb").write_text(json.dumps({"cells": []}))
    (archive / "unsafe name.html").write_text("unsafe")

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Historical readings" in body
    assert "/view/historic-report" in body
    assert "notebook-only" not in body
    assert "/view/.cache-historic" in body
    assert "unsafe name" not in body


def test_historical_bare_url_renders_truthful_archive_page(tmp_path):
    client, archive = _client(tmp_path)
    (archive / "historic-report.html").write_text("<h1>Historic report</h1>")
    (archive / "historic-report.ipynb").write_text(json.dumps({"cells": []}))

    response = client.get("/view/historic-report")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Archived reading" in body
    assert "predates Saturn's current findings format" in body
    assert 'href="/view/historic-report.html"' in body
    assert 'href="/view/historic-report.ipynb"' in body


def test_historical_html_and_notebook_are_served_from_allowlist(tmp_path):
    client, archive = _client(tmp_path)
    html = "<!doctype html><title>historic</title><p>Preserved report</p>"
    notebook = json.dumps({"nbformat": 4, "cells": []})
    (archive / "historic-report.html").write_text(html)
    (archive / "historic-report.ipynb").write_text(notebook)

    html_response = client.get("/view/historic-report.html")
    notebook_response = client.get("/view/historic-report.ipynb")

    assert html_response.status_code == 200
    assert html_response.get_data(as_text=True) == html
    assert html_response.headers["Content-Security-Policy"] == (
        "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; "
        "frame-src 'none'; font-src 'none'; media-src 'none'; object-src 'none'; "
        "form-action 'none'; base-uri 'none'"
    )
    assert notebook_response.status_code == 200
    assert notebook_response.get_json() == {"nbformat": 4, "cells": []}
    assert "attachment" in notebook_response.headers["Content-Disposition"]


def test_missing_or_unsafe_archive_paths_do_not_expose_files(tmp_path):
    client, archive = _client(tmp_path)
    (archive / "private.txt").write_text("secret")
    (archive.parent / "outside.html").write_text("outside")

    assert client.get("/view/missing").status_code == 404
    assert client.get("/view/missing.html").status_code == 404
    assert client.get("/view/private.txt").status_code == 404
    assert client.get("/view/..%2Foutside.html").status_code == 404
    assert client.get("/view/%2e%2e%2Foutside.html").status_code == 404


def test_orphan_archive_files_are_never_indexed_or_served(tmp_path):
    client, archive = _client(tmp_path)
    (archive / "html-only.html").write_text("orphan html")
    (archive / "notebook-only.ipynb").write_text(json.dumps({"cells": []}))

    index = client.get("/").get_data(as_text=True)

    assert "html-only" not in index
    assert "notebook-only" not in index
    for path in (
        "/view/html-only",
        "/view/html-only.html",
        "/view/html-only.ipynb",
        "/view/notebook-only",
        "/view/notebook-only.html",
        "/view/notebook-only.ipynb",
    ):
        assert client.get(path).status_code == 404


def test_symlink_escape_pair_is_never_indexed_or_served(tmp_path):
    client, archive = _client(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.html").write_text("outside html")
    (outside / "escaped.ipynb").write_text(json.dumps({"cells": []}))
    os.symlink(outside / "escaped.html", archive / "escaped.html")
    os.symlink(outside / "escaped.ipynb", archive / "escaped.ipynb")
    os.symlink(outside / "missing.html", archive / "broken.html")
    os.symlink(outside / "missing.ipynb", archive / "broken.ipynb")

    index = client.get("/").get_data(as_text=True)

    assert "escaped" not in index
    assert "broken" not in index
    for artifact_id in ("escaped", "broken"):
        assert client.get(f"/view/{artifact_id}").status_code == 404
        assert client.get(f"/view/{artifact_id}.html").status_code == 404
        assert client.get(f"/view/{artifact_id}.ipynb").status_code == 404


def test_archive_is_optional(tmp_path):
    client = create_app(findings_dir=tmp_path, testing=True).test_client()

    assert client.get("/").status_code == 200
    assert client.get("/view/historic").status_code == 404
