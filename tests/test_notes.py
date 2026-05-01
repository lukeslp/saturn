"""Tests for the per-finding `.notes.md` sidecar."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturn.viewer.app import create_app
from saturn.viewer.notes import notes_path_for, render_notes


# ---------- render_notes ----------------------------------------------------


def test_render_notes_returns_none_when_file_missing(tmp_path: Path):
    assert render_notes(tmp_path / "absent.notes.md") is None


def test_render_notes_returns_none_when_file_empty(tmp_path: Path):
    f = tmp_path / "empty.notes.md"
    f.write_text("")
    assert render_notes(f) is None


def test_render_notes_returns_none_when_file_whitespace_only(tmp_path: Path):
    f = tmp_path / "ws.notes.md"
    f.write_text("   \n\n  \n")
    assert render_notes(f) is None


def test_render_notes_renders_basic_markdown(tmp_path: Path):
    f = tmp_path / "n.notes.md"
    f.write_text("# Heading\n\nA **bold** line with `code`.\n")
    html = render_notes(f)
    assert html is not None
    assert "<h1" in html
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html


def test_render_notes_supports_tables(tmp_path: Path):
    f = tmp_path / "n.notes.md"
    f.write_text(
        "| col | val |\n"
        "| --- | --- |\n"
        "| a   | 1   |\n"
    )
    html = render_notes(f)
    assert html is not None
    assert "<table>" in html
    assert "<th>col</th>" in html
    assert "<td>a</td>" in html


def test_render_notes_supports_fenced_code(tmp_path: Path):
    f = tmp_path / "n.notes.md"
    f.write_text("```\ndef foo():\n    pass\n```\n")
    html = render_notes(f)
    assert html is not None
    assert "<pre>" in html
    assert "def foo()" in html


def test_render_notes_strips_script_tags(tmp_path: Path):
    f = tmp_path / "evil.notes.md"
    f.write_text("hello <script>alert(1)</script> world\n")
    html = render_notes(f)
    assert html is not None
    # Tag is removed; inner text becomes harmless plain text (no script execution).
    assert "<script" not in html
    assert "</script>" not in html
    assert "hello" in html
    assert "world" in html


def test_render_notes_strips_style_tags(tmp_path: Path):
    f = tmp_path / "evil.notes.md"
    f.write_text("hello <style>body{display:none}</style> world\n")
    html = render_notes(f)
    assert html is not None
    assert "<style" not in html
    assert "</style>" not in html


def test_render_notes_strips_inline_event_handlers(tmp_path: Path):
    f = tmp_path / "evil.notes.md"
    f.write_text('<a href="#" onclick="bad()">click</a>\n')
    html = render_notes(f)
    assert html is not None
    assert "onclick" not in html


def test_render_notes_strips_iframes(tmp_path: Path):
    f = tmp_path / "evil.notes.md"
    f.write_text(
        "before iframe\n\n"
        '<iframe src="https://evil.example"></iframe>\n\n'
        "after iframe\n"
    )
    html = render_notes(f)
    assert html is not None
    assert "<iframe" not in html
    assert "evil.example" not in html
    assert "before iframe" in html
    assert "after iframe" in html


def test_render_notes_strips_inline_styles(tmp_path: Path):
    f = tmp_path / "evil.notes.md"
    f.write_text('<p style="color: red">styled</p>\n')
    html = render_notes(f)
    assert html is not None
    assert "style=" not in html
    assert "styled" in html


def test_render_notes_linkifies_bare_urls(tmp_path: Path):
    f = tmp_path / "n.notes.md"
    f.write_text("see https://arxiv.org/abs/1234.5678 for details\n")
    html = render_notes(f)
    assert html is not None
    assert 'href="https://arxiv.org/abs/1234.5678"' in html
    assert 'rel="nofollow noopener"' in html


def test_render_notes_blocks_javascript_protocol(tmp_path: Path):
    f = tmp_path / "evil.notes.md"
    f.write_text('[click](javascript:alert(1))\n')
    html = render_notes(f)
    assert html is not None
    assert "javascript:" not in html


# ---------- notes_path_for ---------------------------------------------------


def test_notes_path_for_builds_sibling_path(tmp_path: Path):
    p = notes_path_for(tmp_path, "demo")
    assert p == tmp_path / "demo.notes.md"


# ---------- view route surfaces notes_html ----------------------------------


def _findings_payload() -> dict:
    return {
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 100, "sampled_rows": 100, "seed": 0,
                 "mode": "full", "generated_at": "2026-05-01T00:00:00+00:00"},
        "schema": {"a": "numeric"},
        "language_counts": {},
        "notes": [],
        "columns": [{
            "column": "a", "kind": "numeric",
            "n": 100, "n_null": 0, "n_unique": 50,
            "stats": {"median": 5.0},
            "extras": {},
            "alerts": [], "null_rate": 0.0,
        }],
    }


@pytest.fixture
def findings_dir(tmp_path: Path) -> Path:
    (tmp_path / "demo.json").write_text(json.dumps(_findings_payload()))
    return tmp_path


@pytest.fixture
def client(findings_dir: Path):
    return create_app(findings_dir=findings_dir, testing=True).test_client()


def test_view_omits_notes_card_when_no_sidecar(client):
    resp = client.get("/view/demo")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "notes-card" not in body


def test_view_renders_notes_card_when_sidecar_present(client, findings_dir: Path):
    (findings_dir / "demo.notes.md").write_text(
        "## Field notes\n\nThis dataset is **incomplete** for our analysis.\n"
    )
    resp = client.get("/view/demo")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "notes-card" in body
    assert "<strong>incomplete</strong>" in body
    assert "demo.notes.md" in body  # the "edit this file" hint


def test_notebook_view_renders_notes_cell_when_sidecar_present(client, findings_dir: Path):
    (findings_dir / "demo.notes.md").write_text("Reviewed 2026-04-23.\n")
    resp = client.get("/view/demo?view=notebook")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "cell-notes" in body
    assert "Reviewed 2026-04-23" in body
