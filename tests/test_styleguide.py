"""Tests for the /styleguide route."""

from __future__ import annotations

import pytest

from saturn.viewer.app import create_app


@pytest.fixture
def client(tmp_path):
    return create_app(findings_dir=tmp_path, testing=True).test_client()


def test_styleguide_renders_200(client):
    resp = client.get("/styleguide")
    assert resp.status_code == 200


def test_styleguide_demonstrates_every_component(client):
    """Style guide must render every component class so live changes show up here."""
    resp = client.get("/styleguide")
    body = resp.get_data(as_text=True)
    # Tokens section
    for token in ["--paper", "--ink", "--hot", "--cool", "--rule"]:
        assert token in body
    # Component classes
    for cls in [
        "chip", "chip-role", "alert-info", "alert-warn", "alert-error",
        "stamp", "verdict", "btn", "byok", "field", "checkbox",
        "cell-md", "cell-code", "cell-output", "cell-figure",
        "summary-card", "schema-table", "df", "dropzone",
        "nb-foot",
    ]:
        assert cls in body, f"styleguide missing demo of {cls!r}"


def test_styleguide_has_landmarks_and_skip_link(client):
    """A11y baseline still applies on the styleguide page itself."""
    import re

    resp = client.get("/styleguide")
    body = resp.get_data(as_text=True)
    assert 'href="#main"' in body
    assert re.search(r'<main\b[^>]*id="main"', body)
    assert len(re.findall(r"<h1[>\s]", body)) == 1


def test_styleguide_lists_principles(client):
    resp = client.get("/styleguide")
    body = resp.get_data(as_text=True)
    assert "Principles" in body
    # Sanity-check on the actual content claims
    assert "Hairlines" in body
    assert "WCAG" in body
