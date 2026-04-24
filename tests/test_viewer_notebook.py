"""Tests for the notebook view (`?view=notebook`)."""

from __future__ import annotations

import json

import pytest

from saturn.viewer.app import create_app


PROFILE_FIXTURE = {
    "saturn_version": "0.2.0",
    "meta": {
        "source": "hf://lukeslp/bluesky-alt-text",
        "row_count": 1000,
        "sampled_rows": 1000,
        "seed": 42,
        "mode": "full",
        "generated_at": "2026-04-23T00:00:00+00:00",
    },
    "schema": {"text_col": "text", "num_col": "numeric"},
    "language_counts": {"en": 900, "es": 100},
    "notes": [],
    "columns": [
        {
            "column": "text_col",
            "kind": "text",
            "n": 1000, "n_null": 50, "n_unique": 900,
            "stats": {"len_mean": 120.0, "duplicate_rate": 0.05},
            "extras": {
                "length_histogram": {"counts": [10, 20, 30], "edges": [0, 50, 100, 200]},
                "language_counts": {"en": 900, "es": 100, "__engine": "fasttext"},
            },
            "alerts": [{"level": "info", "code": "multilingual", "message": "2 languages"}],
            "null_rate": 0.05,
        },
        {
            "column": "num_col",
            "kind": "numeric",
            "n": 1000, "n_null": 0, "n_unique": 800,
            "stats": {"mean": 5.0, "median": 4.0, "std": 1.2, "min": 0.0, "max": 10.0,
                      "q1": 3.5, "q3": 6.0, "iqr": 2.5, "skew": 0.2, "kurtosis": 0.1,
                      "n_outliers": 2, "outlier_rate": 0.002, "zero_rate": 0.0},
            "extras": {
                "histogram": {"counts": [10, 20, 30, 20, 10], "edges": [0, 2, 4, 6, 8, 10]},
                "sample": [1.0, 2.0, 3.0, 4.0, 5.0],
            },
            "alerts": [],
            "null_rate": 0.0,
        },
    ],
    "insights": {
        "providers": ["anthropic:claude-sonnet-4-6"],
        "insights": [
            {
                "scope": "dataset", "target": "__global__",
                "narrative": "DATASET-LEVEL-NARRATIVE",
                "confidence": "high",
                "evidence_keys": ["row_count", "language_counts"],
                "model": "anthropic:claude-sonnet-4-6",
                "critiques": [],
            },
            {
                "scope": "column", "target": "text_col",
                "narrative": "TEXT-COL-NARRATIVE",
                "confidence": "medium",
                "evidence_keys": ["duplicate_rate"],
                "model": "anthropic:claude-sonnet-4-6",
                "critiques": [],
            },
        ],
        "total_usage": {"input_tokens": 1000, "output_tokens": 300, "total_tokens": 1300},
        "errors": [],
    },
}


COMPARE_FIXTURE = {
    "saturn_version": "0.2.0",
    "a": {"label": "curated", "source": "hf://x/a", "row_count": 1000, "schema": {},
          "language_counts": {}},
    "b": {"label": "firehose", "source": "hf://x/b", "row_count": 500, "schema": {},
          "language_counts": {}},
    "columns": [
        {
            "column": "alt_text", "kind": "text",
            "a": {"column": "alt_text", "kind": "text", "n": 1000, "n_null": 0,
                  "n_unique": 900, "stats": {"len_mean": 200.0}, "extras": {},
                  "alerts": [], "null_rate": 0.0},
            "b": {"column": "alt_text", "kind": "text", "n": 500, "n_null": 50,
                  "n_unique": 450, "stats": {"len_mean": 281.0}, "extras": {},
                  "alerts": [], "null_rate": 0.1},
            "delta": {"len_mean_delta": 81.0, "null_rate_delta": 0.1},
            "notes": [],
        }
    ],
    "divergences": [
        {"column": "alt_text", "kind": "text", "score": 0.82,
         "signals": ["len_mean +81", "null +10%"]}
    ],
    "generated_at": "2026-04-23T00:00:00+00:00",
    "insights": {
        "providers": ["anthropic:claude-sonnet-4-6"],
        "insights": [
            {
                "scope": "compare", "target": "__global__",
                "narrative": "COMPARE-DATASET-NARRATIVE",
                "confidence": "high",
                "evidence_keys": ["len_mean_delta"],
                "model": "anthropic:claude-sonnet-4-6",
                "critiques": [],
            }
        ],
        "total_usage": {"input_tokens": 500, "output_tokens": 200, "total_tokens": 700},
        "errors": [],
    },
}


@pytest.fixture
def client(tmp_path):
    (tmp_path / "demo.json").write_text(json.dumps(PROFILE_FIXTURE))
    (tmp_path / "diff.json").write_text(json.dumps(COMPARE_FIXTURE))
    return create_app(findings_dir=tmp_path, testing=True).test_client()


# ---------- dispatch / toggle ------------------------------------------------


def test_notebook_view_selected_via_query_param(client):
    """`?view=notebook` renders the notebook template, not the report."""
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # Markers that only appear in the notebook template
    assert 'class="nb"' in body
    assert "[1]:" in body or "[2]:" in body  # cell gutter prompts
    assert "<figcaption" in body  # figure captions


def test_report_is_default_view(client):
    resp = client.get("/view/demo")
    body = resp.get_data(as_text=True)
    # Datasheet-annual markers; NOT the notebook cell shell
    assert 'class="narrative"' in body
    assert 'class="nb"' not in body


def test_invalid_view_param_falls_back_to_report(client):
    resp = client.get("/view/demo?view=garbage")
    body = resp.get_data(as_text=True)
    assert 'class="narrative"' in body
    assert 'class="nb"' not in body


def test_view_toggle_links_present_on_report(client):
    resp = client.get("/view/demo")
    body = resp.get_data(as_text=True)
    # Report view should link outward to notebook
    assert "?view=notebook" in body


def test_view_toggle_links_present_on_notebook(client):
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    # Notebook should link back to report
    assert "?view=report" in body or 'href="/view/demo"' in body


# ---------- content coverage -------------------------------------------------


def test_notebook_renders_dataset_insight(client):
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert "DATASET-LEVEL-NARRATIVE" in body
    # confidence badge
    assert "conf-high" in body


def test_notebook_renders_column_insight(client):
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert "TEXT-COL-NARRATIVE" in body


def test_notebook_renders_reproduction_code_cell(client):
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert "saturn-dissect" in body
    # HF source gets a `saturn huggingface ...` snippet
    assert "huggingface" in body
    assert "lukeslp/bluesky-alt-text" in body


def test_notebook_renders_reproducibility_footer(client):
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert "saturn-insight-v1" in body
    assert "v0.2.0" in body
    # tokens stat
    assert "1,300" in body or "1300" in body


def test_notebook_has_per_column_figures_when_charts_available(client):
    """Cell gutters tagged `Fig N.` for plot cells."""
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    # dataset overview fig is always first
    assert "Fig&nbsp;1." in body or "Fig 1." in body


def test_notebook_anchors_link_schema_to_column_cells(client):
    """Clicking a column in the schema table jumps to that column's cell."""
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert "#cell-col-text_col" in body
    assert 'id="cell-col-text_col"' in body


# ---------- compare notebook -------------------------------------------------


def test_compare_notebook_renders_pair_summary(client):
    resp = client.get("/view/diff?view=notebook")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "COMPARE-DATASET-NARRATIVE" in body
    assert "curated" in body and "firehose" in body


def test_compare_notebook_renders_divergence_table(client):
    resp = client.get("/view/diff?view=notebook")
    body = resp.get_data(as_text=True)
    assert "divergence_summary" in body
    assert "len_mean +81" in body


def test_compare_notebook_per_column_delta_table(client):
    resp = client.get("/view/diff?view=notebook")
    body = resp.get_data(as_text=True)
    assert "alt_text" in body
    assert "len_mean_delta" in body
    # cell anchors work for compare too
    assert 'id="cell-col-alt_text"' in body


# ---------- a11y / structure -------------------------------------------------


def test_notebook_has_landmarks_and_single_h1(client):
    import re
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert 'href="#main"' in body
    assert re.search(r"<main\b[^>]*id=\"main\"", body)
    assert len(re.findall(r"<h1[>\s]", body)) == 1


def test_notebook_figures_wrap_in_figure_tags(client):
    import re
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    # Every figcaption should live inside a <figure>
    figures = re.findall(r"<figure\b.*?</figure>", body, re.DOTALL)
    for fig in figures:
        assert "<figcaption" in fig
