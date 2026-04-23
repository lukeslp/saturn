"""WCAG 2.2 AA structural smoke tests for the viewer.

These are not a substitute for axe/pa11y/manual screen-reader testing —
they catch the regressions that cost nothing to prevent: missing lang,
duplicate h1s, tables without captions, absent skip-links.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from saturn.viewer.app import create_app


@pytest.fixture
def findings_dir(tmp_path):
    (tmp_path / "one.json").write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "meta": {
                    "source": "s",
                    "row_count": 1,
                    "sampled_rows": 1,
                    "seed": 0,
                    "mode": "full",
                    "generated_at": "2026-04-22T00:00:00+00:00",
                },
                "schema": {"a": "numeric"},
                "language_counts": {},
                "notes": [],
                "columns": [
                    {
                        "column": "a",
                        "kind": "numeric",
                        "n": 1,
                        "n_null": 0,
                        "n_unique": 1,
                        "stats": {},
                        "extras": {},
                        "alerts": [],
                        "null_rate": 0.0,
                    }
                ],
            }
        )
    )
    (tmp_path / "two.json").write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "a": {"label": "L", "source": "s", "row_count": 1, "schema": {}, "language_counts": {}},
                "b": {"label": "R", "source": "s", "row_count": 1, "schema": {}, "language_counts": {}},
                "columns": [],
                "divergences": [
                    {"column": "x", "kind": "numeric", "score": 0.5, "signals": ["mean +2"]}
                ],
                "generated_at": "2026-04-22T00:00:00+00:00",
            }
        )
    )
    return tmp_path


@pytest.fixture
def client(findings_dir):
    return create_app(findings_dir=findings_dir, testing=True).test_client()


def _fetch(client, path: str) -> str:
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    return resp.get_data(as_text=True)


PATHS = ["/", "/view/one", "/view/two"]


@pytest.mark.parametrize("path", PATHS)
def test_pages_have_lang_attr(client, path):
    html = _fetch(client, path)
    assert re.search(r"<html[^>]+\blang=", html), "root <html> must carry a lang attribute"


@pytest.mark.parametrize("path", PATHS)
def test_pages_have_single_h1(client, path):
    html = _fetch(client, path)
    h1_count = len(re.findall(r"<h1[>\s]", html))
    assert h1_count == 1, f"{path} has {h1_count} <h1> elements, WCAG expects exactly 1"


@pytest.mark.parametrize("path", PATHS)
def test_tables_have_captions(client, path):
    html = _fetch(client, path)
    tables = re.findall(r"<table\b.*?</table>", html, flags=re.DOTALL)
    for t in tables:
        assert "<caption" in t, f"{path} has a table without <caption>"


@pytest.mark.parametrize("path", PATHS)
def test_has_skip_link_and_main_landmark(client, path):
    html = _fetch(client, path)
    assert 'href="#main"' in html, f"{path} missing skip-link"
    assert re.search(r'<main\b[^>]*id="main"', html), f"{path} missing <main id=\"main\">"


@pytest.mark.parametrize("path", PATHS)
def test_buttons_are_real_buttons_not_links(client, path):
    """Sort UI elements must be <button>, not styled <a>, for keyboard + screen-reader semantics."""
    html = _fetch(client, path)
    # Any element with data-sort must be a button
    for match in re.finditer(r'<([a-z]+)[^>]+data-sort=', html):
        assert match.group(1) == "button", f"data-sort on <{match.group(1)}>, must be <button>"


def test_skip_link_precedes_main(client):
    html = _fetch(client, "/")
    skip_idx = html.find('href="#main"')
    main_idx = html.find("<main")
    assert 0 <= skip_idx < main_idx, "skip-link must appear before <main>"
