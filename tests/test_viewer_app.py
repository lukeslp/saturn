"""Tests for the Flask viewer app factory and core routes."""

from __future__ import annotations

import json

import pytest

from saturn.viewer.app import create_app


@pytest.fixture
def findings_dir(tmp_path):
    (tmp_path / "demo.json").write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "meta": {
                    "source": "hf://demo/set",
                    "row_count": 100,
                    "sampled_rows": 100,
                    "seed": 42,
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
                        "n": 100,
                        "n_null": 0,
                        "n_unique": 50,
                        "stats": {},
                        "extras": {},
                        "alerts": [],
                        "null_rate": 0.0,
                    }
                ],
            }
        )
    )
    return tmp_path


@pytest.fixture
def client(findings_dir):
    app = create_app(findings_dir=findings_dir, testing=True)
    return app.test_client()


def test_index_lists_findings(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "demo" in body
    assert "hf://demo/set" in body


def test_index_has_landmark_and_skip_link(client):
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    assert 'href="#main"' in body
    assert "<main" in body and 'id="main"' in body


def test_unknown_findings_returns_404(client):
    resp = client.get("/view/does-not-exist")
    assert resp.status_code == 404


def test_health_endpoint(client, findings_dir):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.is_json
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["findings_dir"] == str(findings_dir)


def test_index_when_directory_empty(tmp_path):
    app = create_app(findings_dir=tmp_path, testing=True)
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "No findings" in body or "no findings" in body.lower()


def test_profile_view_renders_columns(client, findings_dir):
    resp = client.get("/view/demo")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "hf://demo/set" in body
    assert 'scope="col"' in body


def test_profile_view_deep_links_each_column(client):
    resp = client.get("/view/demo")
    body = resp.get_data(as_text=True)
    assert 'id="col-a"' in body


def test_profile_view_exposes_json_via_api(client):
    resp = client.get("/api/findings/demo")
    assert resp.status_code == 200
    assert resp.is_json
    data = resp.get_json()
    assert data["meta"]["source"] == "hf://demo/set"
