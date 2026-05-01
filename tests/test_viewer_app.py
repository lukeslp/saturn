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
    # Empty state copy in the redesigned index
    assert "No readings yet" in body


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


def test_compare_view_renders_both_sides(client, findings_dir):
    (findings_dir / "diff.json").write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "a": {
                    "label": "curated",
                    "source": "hf://x/y[curated]",
                    "row_count": 279000,
                    "schema": {"alt_text": "text"},
                    "language_counts": {},
                },
                "b": {
                    "label": "firehose",
                    "source": "hf://x/y[firehose]",
                    "row_count": 125000,
                    "schema": {"alt_text": "text"},
                    "language_counts": {},
                },
                "columns": [
                    {
                        "column": "alt_text",
                        "kind": "text",
                        "a": {
                            "column": "alt_text",
                            "kind": "text",
                            "n": 279000,
                            "n_null": 0,
                            "n_unique": 250000,
                            "stats": {},
                            "extras": {},
                            "alerts": [],
                            "null_rate": 0.0,
                        },
                        "b": {
                            "column": "alt_text",
                            "kind": "text",
                            "n": 125000,
                            "n_null": 0,
                            "n_unique": 120000,
                            "stats": {},
                            "extras": {},
                            "alerts": [],
                            "null_rate": 0.0,
                        },
                        "delta": {"len_mean_delta": 79.0},
                        "notes": [],
                    }
                ],
                "divergences": [
                    {"column": "alt_text", "kind": "text", "score": 0.82, "signals": ["len_mean +79"]}
                ],
                "generated_at": "2026-04-22T00:00:00+00:00",
            }
        )
    )
    resp = client.get("/view/diff")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "curated" in body and "firehose" in body
    assert "279,000" in body
    assert "len_mean" in body


def test_compare_view_has_caption_and_divergence_summary(client, findings_dir):
    # uses the "diff" fixture created by the previous test's fixture usage is not persistent;
    # build a self-contained one here
    (findings_dir / "d2.json").write_text(
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
    resp = client.get("/view/d2")
    body = resp.get_data(as_text=True)
    assert "Most divergent" in body
    # Divergence summary is semantic <ol class="dv-list"> in the redesigned view
    assert 'class="dv-list"' in body
    assert "mean +2" in body


def test_view_rejects_path_traversal_in_id(tmp_path):
    """Even if Flask's converter changes, _safe_findings_path must block escapes."""
    secret = tmp_path.parent / "secret.json"
    secret.write_text('{"meta": {}, "columns": []}')
    try:
        app = create_app(findings_dir=tmp_path, testing=True)
        client = app.test_client()

        # Directly exercise the helper: a concocted id that would escape if unchecked
        from saturn.viewer.app import _safe_findings_path

        escaped = _safe_findings_path(tmp_path, "../secret")
        assert escaped is None, "traversal not blocked"

        # The live routes still return 404 for missing files (no traversal reachable
        # via Flask's default string converter, so the status is the same either way)
        resp = client.get("/view/..")
        assert resp.status_code == 404
    finally:
        secret.unlink(missing_ok=True)


def test_api_findings_rejects_path_traversal_in_id(tmp_path):
    from saturn.viewer.app import _safe_findings_path

    outside = _safe_findings_path(tmp_path, "../../../etc/passwd")
    assert outside is None


def test_prefix_middleware_honors_x_forwarded_prefix_when_trusted(tmp_path, monkeypatch):
    """When SATURN_TRUST_FORWARDED_PREFIX=1, url_for must prepend the forwarded prefix."""
    import json

    (tmp_path / "demo.json").write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0,
                         "mode": "full", "generated_at": "2026-04-23T00:00:00+00:00"},
                "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
                "columns": [{"column": "a", "kind": "numeric", "n": 1, "n_null": 0,
                             "n_unique": 1, "stats": {}, "extras": {}, "alerts": [],
                             "null_rate": 0.0}],
            }
        )
    )
    monkeypatch.setenv("SATURN_TRUST_FORWARDED_PREFIX", "1")
    app = create_app(findings_dir=tmp_path, testing=True)
    client = app.test_client()
    resp = client.get("/", headers={"X-Forwarded-Prefix": "/saturn"})
    body = resp.get_data(as_text=True)
    # The url_for('view', id='demo') call must prefix with /saturn
    assert 'href="/saturn/view/demo"' in body, body[:500]


def test_prefix_middleware_ignores_header_when_not_trusted(tmp_path, monkeypatch):
    """Without the trust flag, a spoofed X-Forwarded-Prefix must be ignored."""
    import json

    (tmp_path / "demo.json").write_text(
        json.dumps(
            {
                "saturn_version": "0.1.0",
                "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0,
                         "mode": "full", "generated_at": "2026-04-23T00:00:00+00:00"},
                "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
                "columns": [{"column": "a", "kind": "numeric", "n": 1, "n_null": 0,
                             "n_unique": 1, "stats": {}, "extras": {}, "alerts": [],
                             "null_rate": 0.0}],
            }
        )
    )
    monkeypatch.delenv("SATURN_TRUST_FORWARDED_PREFIX", raising=False)
    app = create_app(findings_dir=tmp_path, testing=True)
    client = app.test_client()
    resp = client.get("/", headers={"X-Forwarded-Prefix": "/hacker"})
    body = resp.get_data(as_text=True)
    assert 'href="/hacker/' not in body
    assert 'href="/view/demo"' in body  # plain root, no prefix


def test_index_renders_filter_controls_when_findings_exist(client):
    """Filter UI surfaces only when there are readings to filter."""
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    assert 'id="readings-filter"' in body
    assert 'name="kind"' in body
    assert 'index-filter.js' in body
    # data attributes drive the JS — verify they're emitted on each article
    assert 'data-name=' in body
    assert 'data-kind=' in body
    assert 'data-source=' in body


def test_index_omits_filter_when_no_findings(tmp_path):
    """No findings → no filter UI (less to scan past for empty installs)."""
    app = create_app(findings_dir=tmp_path, testing=True)
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'id="readings-filter"' not in body
    assert "No readings yet" in body


def test_filter_js_progressive_enhancement(client):
    """The pager hides via the [hidden] attribute by default — page works without JS."""
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    # readings-pager element exists but starts hidden; JS reveals when needed
    assert 'class="readings-pager"' in body
    # readings-empty also starts hidden
    assert 'id="readings-empty"' in body
