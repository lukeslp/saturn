"""Tests for the Plotly a11y data-table fallbacks."""

from __future__ import annotations

import json

import pytest

from saturn.viewer.app import create_app
from saturn.viewer.chart_fallback import (
    column_data_table,
    correlation_data_table,
    language_data_table,
    overview_data_table,
)


# ---------- column_data_table ------------------------------------------------


def test_numeric_column_emits_histogram_table():
    col = {
        "column": "score", "kind": "numeric",
        "n": 100, "n_null": 0, "n_unique": 90,
        "stats": {"median": 5.0},
        "extras": {"histogram": {"counts": [10, 30, 40, 20], "edges": [0, 2.5, 5, 7.5, 10]}},
    }
    table = column_data_table(col)
    assert table["headers"] == ["bin", "count"]
    assert len(table["rows"]) == 4
    # First row spans the first bin
    assert "0" in table["rows"][0][0]
    assert "2.5" in table["rows"][0][0]
    assert table["rows"][0][1] == 10


def test_numeric_column_with_no_histogram_returns_none():
    col = {"column": "x", "kind": "numeric", "extras": {}}
    assert column_data_table(col) is None


def test_categorical_column_emits_top_values_with_share():
    col = {
        "column": "kind", "kind": "categorical",
        "n": 100, "n_unique": 3,
        "stats": {},
        "extras": {"top_values": [["a", 60], ["b", 30], ["c", 10]]},
    }
    table = column_data_table(col)
    assert table["headers"] == ["value", "count", "share"]
    assert table["rows"][0] == ["a", 60, "60.0%"]


def test_text_column_prefers_length_histogram():
    col = {
        "column": "text", "kind": "text",
        "n": 100,
        "stats": {"len_mean": 50.0},
        "extras": {
            "length_histogram": {"counts": [20, 30, 50], "edges": [0, 50, 100, 200]},
            "top_words": [["hello", 80], ["world", 60]],
        },
    }
    table = column_data_table(col)
    # Should use length_histogram (chars), not the top_words branch
    assert "chars" in table["headers"]
    assert table["headers"][1] == "count"


def test_text_column_falls_back_to_top_words_when_no_length_hist():
    col = {
        "column": "text", "kind": "text",
        "extras": {"top_words": [["the", 100], ["and", 50]]},
    }
    table = column_data_table(col)
    assert table["headers"] == ["word", "count"]
    assert table["rows"][0] == ["the", 100]


def test_unknown_kind_returns_none():
    col = {"column": "x", "kind": "boolean", "extras": {}}
    assert column_data_table(col) is None


# ---------- overview / language / correlation -------------------------------


def test_overview_data_table():
    cols = [
        {"column": "a", "kind": "numeric", "null_rate": 0.1},
        {"column": "b", "kind": "text", "null_rate": 0.0},
    ]
    table = overview_data_table(cols)
    assert table["headers"] == ["column", "kind", "null %"]
    assert table["rows"] == [["a", "numeric", "10.0%"], ["b", "text", "0.0%"]]


def test_language_data_table_strips_engine_keys():
    counts = {"en": 900, "es": 100, "__engine": "fasttext:1000"}
    table = language_data_table(counts)
    assert all(row[0] != "__engine" for row in table["rows"])
    # Sorted descending by count
    assert table["rows"][0][0] == "en"


def test_language_data_table_returns_none_when_empty():
    assert language_data_table({}) is None
    assert language_data_table({"__engine": "x"}) is None


def test_correlation_data_table_caps_at_12():
    labels = [f"col_{i}" for i in range(20)]
    corr = [[1.0 if i == j else 0.5 for j in range(20)] for i in range(20)]
    table = correlation_data_table(corr, labels)
    # Headers: leading empty cell + 12 labels
    assert len(table["headers"]) == 13
    # Each row: leading row label + 12 values
    assert all(len(row) == 13 for row in table["rows"])


# ---------- live route renders <details>Show data table</details> -----------


@pytest.fixture
def client(tmp_path):
    (tmp_path / "demo.json").write_text(json.dumps({
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 100, "sampled_rows": 100, "seed": 0,
                 "mode": "full", "generated_at": "2026-05-01T00:00:00+00:00"},
        "schema": {"a": "numeric"},
        "language_counts": {"en": 80, "es": 20},
        "notes": [],
        "columns": [{
            "column": "a", "kind": "numeric",
            "n": 100, "n_null": 0, "n_unique": 50,
            "stats": {"median": 5.0},
            "extras": {"histogram": {"counts": [20, 50, 30], "edges": [0, 3, 6, 10]},
                       "sample": [1, 2, 3]},
            "alerts": [], "null_rate": 0.0,
        }],
    }))
    return create_app(findings_dir=tmp_path, testing=True).test_client()


def test_notebook_renders_chart_fallback(client):
    """Each chart should have a Show-data-table fallback alongside it."""
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert 'class="chart-fallback"' in body
    assert "Show data table" in body
    # The numeric column's histogram bins land in the table
    assert "bin" in body
    # Language fallback table appears too (we set language_counts in fixture)
    assert "lang" in body.lower()


def test_chart_fallback_marked_with_caption_for_a11y(client):
    """Each fallback table has a visually-hidden <caption> for screen readers."""
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    # Caption text from overview_data_table()
    assert "Per-column null rate" in body
