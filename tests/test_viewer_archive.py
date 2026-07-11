"""Regression coverage for safely serving pre-contract Saturn artifacts."""

from __future__ import annotations

import json
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
    assert html_response.headers["Content-Security-Policy"] == "sandbox allow-scripts"
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


def test_archive_is_optional(tmp_path):
    client = create_app(findings_dir=tmp_path, testing=True).test_client()

    assert client.get("/").status_code == 200
    assert client.get("/view/historic").status_code == 404
