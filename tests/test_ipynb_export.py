"""Tests for the .ipynb export."""

from __future__ import annotations

import json

import pytest

from saturn.viewer.app import create_app
from saturn.viewer.ipynb import to_ipynb
from saturn.viewer.loader import FindingsKind, load_findings


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
    "schema": {"text_col": "text", "num_col": "numeric", "cat_col": "categorical"},
    "language_counts": {"en": 900},
    "notes": [],
    "columns": [
        {
            "column": "num_col", "kind": "numeric",
            "n": 1000, "n_null": 0, "n_unique": 800,
            "stats": {"mean": 5.0, "median": 4.0, "std": 1.2},
            "extras": {"histogram": {"counts": [10, 20, 30], "edges": [0, 5, 10, 15]}, "sample": [1, 2, 3]},
            "alerts": [], "null_rate": 0.0,
        },
        {
            "column": "text_col", "kind": "text",
            "n": 1000, "n_null": 50, "n_unique": 900,
            "stats": {"len_mean": 120.0, "duplicate_rate": 0.05},
            "extras": {"length_histogram": {"counts": [5, 10, 15], "edges": [0, 50, 100, 150]}},
            "alerts": [{"level": "info", "code": "multilingual", "message": "2 languages"}],
            "null_rate": 0.05,
        },
        {
            "column": "cat_col", "kind": "categorical",
            "n": 1000, "n_null": 0, "n_unique": 5,
            "stats": {"top_rate": 0.4, "entropy": 1.5},
            "extras": {"top_values": [["a", 400], ["b", 250], ["c", 200], ["d", 100], ["e", 50]]},
            "alerts": [], "null_rate": 0.0,
        },
    ],
    "insights": {
        "providers": ["anthropic:claude-sonnet-4-6"],
        "insights": [
            {
                "scope": "dataset", "target": "__global__",
                "narrative": "GLOBAL-NARRATIVE",
                "confidence": "high",
                "evidence_keys": ["row_count"],
                "model": "anthropic:claude-sonnet-4-6",
                "critiques": [],
            },
            {
                "scope": "column", "target": "num_col",
                "narrative": "NUM-COL-NARRATIVE",
                "confidence": "medium",
                "evidence_keys": [],
                "model": "anthropic:claude-sonnet-4-6",
                "critiques": [],
            },
        ],
        "total_usage": {"input_tokens": 1000, "output_tokens": 300, "total_tokens": 1300},
        "errors": [],
    },
}


@pytest.fixture
def profile_doc(tmp_path):
    p = tmp_path / "demo.json"
    p.write_text(json.dumps(PROFILE_FIXTURE))
    return load_findings(p)


# ---------- structure --------------------------------------------------------


def test_export_returns_valid_nbformat(profile_doc):
    nb = to_ipynb(profile_doc)
    assert nb["nbformat"] == 4
    assert nb["nbformat_minor"] == 5
    assert "kernelspec" in nb["metadata"]
    assert nb["metadata"]["kernelspec"]["name"] == "python3"
    assert "cells" in nb and isinstance(nb["cells"], list)
    assert nb["cells"], "must have at least one cell"


def test_every_cell_has_required_fields(profile_doc):
    nb = to_ipynb(profile_doc)
    for cell in nb["cells"]:
        assert cell["cell_type"] in {"markdown", "code"}
        assert "source" in cell
        assert isinstance(cell["source"], list)  # nbformat expects line lists
        if cell["cell_type"] == "code":
            assert "outputs" in cell
            assert cell["outputs"] == []


def test_first_cell_is_markdown_overview(profile_doc):
    nb = to_ipynb(profile_doc)
    first = nb["cells"][0]
    assert first["cell_type"] == "markdown"
    text = "".join(first["source"])
    assert "demo" in text.lower() or "bluesky" in text
    assert "1,000" in text
    assert "v0.2.0" in text


def test_load_cell_references_findings_filename(profile_doc):
    nb = to_ipynb(profile_doc)
    code = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "json.load" in code
    assert "demo.json" in code


def test_dataset_narrative_appears_as_markdown(profile_doc):
    nb = to_ipynb(profile_doc)
    text = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown")
    assert "GLOBAL-NARRATIVE" in text


def test_per_column_narrative_appears(profile_doc):
    nb = to_ipynb(profile_doc)
    text = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown")
    assert "NUM-COL-NARRATIVE" in text


def test_each_kind_gets_a_plot_cell(profile_doc):
    nb = to_ipynb(profile_doc)
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    plot_cells = [c for c in code if "matplotlib" in "".join(c["source"])]
    # numeric, text, categorical → one plot each
    assert len(plot_cells) == 3
    plot_text = "\n".join("".join(c["source"]) for c in plot_cells)
    assert "histogram" in plot_text  # numeric/text
    assert "top_values" in plot_text  # categorical


def test_reproducibility_footer_includes_versions(profile_doc):
    nb = to_ipynb(profile_doc)
    text = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown")
    assert "saturn-insight-v1" in text
    assert "v0.2.0" in text
    assert "1,300" in text  # tokens


# ---------- compare ----------------------------------------------------------


COMPARE_FIXTURE = {
    "saturn_version": "0.2.0",
    "a": {"label": "curated", "source": "hf://x/a", "row_count": 1000, "schema": {}, "language_counts": {}},
    "b": {"label": "firehose", "source": "hf://x/b", "row_count": 500, "schema": {}, "language_counts": {}},
    "columns": [
        {"column": "alt_text", "kind": "text",
         "a": None, "b": None,
         "delta": {"len_mean_delta": 81.0}, "notes": []},
    ],
    "divergences": [
        {"column": "alt_text", "kind": "text", "score": 0.82, "signals": ["len_mean +81"]},
    ],
    "generated_at": "2026-04-23T00:00:00+00:00",
    "insights": {
        "providers": ["anthropic:claude-sonnet-4-6"],
        "insights": [
            {"scope": "compare", "target": "__global__",
             "narrative": "COMPARE-NARRATIVE",
             "confidence": "high", "evidence_keys": [],
             "model": "anthropic:claude-sonnet-4-6", "critiques": []},
        ],
        "total_usage": {"input_tokens": 500, "output_tokens": 200, "total_tokens": 700},
        "errors": [],
    },
}


def test_compare_export_dispatch(tmp_path):
    p = tmp_path / "diff.json"
    p.write_text(json.dumps(COMPARE_FIXTURE))
    doc = load_findings(p)
    assert doc.kind is FindingsKind.COMPARE
    nb = to_ipynb(doc)
    text = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown")
    assert "curated" in text and "firehose" in text
    assert "COMPARE-NARRATIVE" in text
    assert "len_mean +81" in text


# ---------- nbformat library validates the output (real safety net) ---------


def test_nbformat_validates_export(profile_doc):
    nbf = pytest.importorskip("nbformat")
    nb = to_ipynb(profile_doc)
    nb_node = nbf.from_dict(nb)
    nbf.validate(nb_node)  # raises on malformed structure


# ---------- route ------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    (tmp_path / "demo.json").write_text(json.dumps(PROFILE_FIXTURE))
    return create_app(findings_dir=tmp_path, testing=True)


def test_ipynb_route_returns_attachment(app):
    resp = app.test_client().get("/view/demo.ipynb")
    assert resp.status_code == 200
    assert resp.mimetype == "application/x-ipynb+json"
    assert 'attachment; filename="demo.ipynb"' in resp.headers["Content-Disposition"]
    body = json.loads(resp.get_data(as_text=True))
    assert body["nbformat"] == 4


def test_ipynb_route_404_on_missing(app):
    resp = app.test_client().get("/view/does-not-exist.ipynb")
    assert resp.status_code == 404


def test_ipynb_route_path_traversal_blocked(app):
    """The .ipynb route uses _safe_findings_path → 404 on escape attempts."""
    resp = app.test_client().get("/view/..%2fpasswd.ipynb")
    # Flask string converter forbids '/', so the URL won't even match the route.
    # Either 404 (no route) or 404 (file not found). Both are fine.
    assert resp.status_code == 404
