# Phase 5 — Flask Viewer (port 5043) Implementation Plan

> Archived implementation plan. Steps use checkbox (`- [ ]`) syntax to preserve the original progress record.

**Goal:** A small Flask service on port 5043 that reads a saturn findings JSON file (single-dataset or compare) and serves an interactive, accessible web view — a live alternative to the static HTML report, so reviewers can sort/filter/deep-link without regenerating.

**Architecture:** A single Flask app (`saturn/viewer/app.py`) loads one or more findings files from a configurable directory, exposes server-rendered Jinja routes for overview/per-column/compare views, and a tiny JSON API (`/api/findings/<id>`) for client-side filtering. No database — findings files on disk are the source of truth. The existing `saturn.report` Jinja templates are shared between the static HTML report and the live viewer via a common base, so any visual fix lands in both places.

**Tech Stack:**
- Flask 3.x + Jinja2 (Jinja already used by `saturn.report`)
- Vanilla JS for sort/filter/deep-link (no build step, keeps saturn pip-installable)
- Plotly.js loaded once from CDN (live mode accepts CDN; the static report stays self-contained)
- WCAG 2.2 AA — this is a saturn product requirement
- pytest + Flask test client (no browser required for CI)

---

## Pre-flight

- [ ] **Step P1: Create worktree**

```bash
cd /home/coolhand/projects/saturn/saturn
git status   # clean
git checkout -b phase5-flask-viewer
```

- [ ] **Step P2: Confirm port 5043 is free**

```bash
lsof -i :5043 || echo "port 5043 free"
sm status | grep 5043 || echo "no service registered on 5043"
```

If either reports a collision, stop and pick the next free port; update `DEFAULT_PORT` in `saturn/viewer/app.py` and every doc reference before continuing.

- [ ] **Step P3: Add Flask to `[web]` extra and install**

```bash
# edit pyproject.toml (details in Task 0)
pip install -e '.[web,dev]'
python -c "import flask; print(flask.__version__)"
```

Expected: Flask ≥ 3.0.

---

## Task 0: Declare `[web]` extra

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 0.1: Add extras section**

In `pyproject.toml`, under `[project.optional-dependencies]`:

```toml
web = [
    "flask>=3.0",
    "werkzeug>=3.0",
]
all = [
    "saturn-dissect[nlp,llm,web,dev]",
]
```

(The existing `all` line — update it — do not duplicate.)

- [ ] **Step 0.2: Install and smoke-test**

```bash
pip install -e '.[web,dev]'
pytest -q  # existing suite still passes
```

- [ ] **Step 0.3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add [web] optional dependency for phase 5 viewer"
```

---

## Task 1: Findings loader (pure, reusable)

**Files:**
- Create: `saturn/viewer/__init__.py`
- Create: `saturn/viewer/loader.py`
- Test: `tests/test_viewer_loader.py`

**Why its own module:** the server is a thin wrapper around this. Separating the loader means the CLI (`saturn serve`), the Flask view functions, and tests all speak the same language: files on disk → validated `FindingsDoc` objects.

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_viewer_loader.py
import json
from pathlib import Path

import pytest

from saturn.viewer.loader import FindingsDoc, FindingsKind, load_findings, list_findings


def _write_profile_findings(path: Path) -> None:
    path.write_text(json.dumps({
        "saturn_version": "0.1.0",
        "meta": {"source": "hf://t/d", "row_count": 100, "sampled_rows": 100,
                 "seed": 42, "mode": "full", "generated_at": "2026-04-22T00:00:00+00:00"},
        "schema": {"a": "numeric"},
        "language_counts": {},
        "notes": [],
        "columns": [{"column": "a", "kind": "numeric", "n": 100, "n_null": 0,
                     "n_unique": 50, "stats": {"mean": 1.0}, "extras": {},
                     "alerts": [], "null_rate": 0.0}],
    }))


def _write_compare_findings(path: Path) -> None:
    path.write_text(json.dumps({
        "saturn_version": "0.1.0",
        "a": {"label": "A", "source": "hf://t/a", "row_count": 100,
              "schema": {"a": "numeric"}, "language_counts": {}},
        "b": {"label": "B", "source": "hf://t/b", "row_count": 50,
              "schema": {"a": "numeric"}, "language_counts": {}},
        "columns": [{"column": "a", "kind": "numeric",
                     "a": None, "b": None, "delta": {}, "notes": []}],
        "divergences": [],
        "generated_at": "2026-04-22T00:00:00+00:00",
    }))


def test_load_profile_findings(tmp_path):
    p = tmp_path / "r.json"
    _write_profile_findings(p)
    doc = load_findings(p)
    assert isinstance(doc, FindingsDoc)
    assert doc.kind == FindingsKind.PROFILE
    assert doc.meta["source"] == "hf://t/d"
    assert len(doc.columns) == 1


def test_load_compare_findings(tmp_path):
    p = tmp_path / "c.json"
    _write_compare_findings(p)
    doc = load_findings(p)
    assert doc.kind == FindingsKind.COMPARE
    assert doc.a_label == "A"
    assert doc.b_label == "B"


def test_load_rejects_non_saturn_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"unrelated": true}')
    with pytest.raises(ValueError, match="not a saturn findings"):
        load_findings(p)


def test_list_findings_returns_sorted_by_mtime_desc(tmp_path):
    f1 = tmp_path / "one.json"
    f2 = tmp_path / "two.json"
    _write_profile_findings(f1)
    _write_compare_findings(f2)
    # Force f2 newer
    import os, time
    time.sleep(0.01)
    os.utime(f2, None)
    listed = list_findings(tmp_path)
    assert [d.id for d in listed] == ["two", "one"]
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `pytest tests/test_viewer_loader.py -v`
Expected: FAIL — module not found.

- [ ] **Step 1.3: Write minimal implementation**

```python
# saturn/viewer/__init__.py
"""Saturn web viewer (Phase 5)."""
```

```python
# saturn/viewer/loader.py
"""Load + validate saturn findings JSON files for the viewer."""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FindingsKind(str, enum.Enum):
    PROFILE = "profile"
    COMPARE = "compare"


@dataclass
class FindingsDoc:
    id: str
    path: Path
    kind: FindingsKind
    raw: dict[str, Any]

    # Profile fields (None for compare)
    meta: dict[str, Any] | None = None
    columns: list[dict[str, Any]] | None = None

    # Compare fields (None for profile)
    a_label: str | None = None
    b_label: str | None = None


def _classify(payload: dict[str, Any]) -> FindingsKind:
    if "columns" in payload and "meta" in payload and "a" not in payload:
        return FindingsKind.PROFILE
    if "a" in payload and "b" in payload and "columns" in payload:
        return FindingsKind.COMPARE
    raise ValueError("not a saturn findings document (missing expected top-level keys)")


def load_findings(path: Path) -> FindingsDoc:
    path = Path(path)
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"not a saturn findings document: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("not a saturn findings document (root is not an object)")
    kind = _classify(payload)
    doc = FindingsDoc(id=path.stem, path=path, kind=kind, raw=payload)
    if kind is FindingsKind.PROFILE:
        doc.meta = payload["meta"]
        doc.columns = payload["columns"]
    else:
        doc.a_label = payload["a"].get("label", "A")
        doc.b_label = payload["b"].get("label", "B")
    return doc


def list_findings(directory: Path) -> list[FindingsDoc]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    paths = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    docs: list[FindingsDoc] = []
    for p in paths:
        try:
            docs.append(load_findings(p))
        except ValueError:
            continue  # skip non-saturn JSON in the dir — don't crash the index
    return docs
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `pytest tests/test_viewer_loader.py -v`
Expected: 4 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add saturn/viewer/ tests/test_viewer_loader.py
git commit -m "feat: findings loader for phase 5 viewer"
```

---

## Task 2: Flask app factory + index route

**Files:**
- Create: `saturn/viewer/app.py`
- Create: `saturn/viewer/templates/base.html.j2`
- Create: `saturn/viewer/templates/index.html.j2`
- Test: `tests/test_viewer_app.py`

- [ ] **Step 2.1: Write the failing test**

```python
# tests/test_viewer_app.py
import json
from pathlib import Path

import pytest

from saturn.viewer.app import create_app


@pytest.fixture
def findings_dir(tmp_path):
    (tmp_path / "demo.json").write_text(json.dumps({
        "saturn_version": "0.1.0",
        "meta": {"source": "hf://demo/set", "row_count": 100, "sampled_rows": 100,
                 "seed": 42, "mode": "full", "generated_at": "2026-04-22T00:00:00+00:00"},
        "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
        "columns": [{"column": "a", "kind": "numeric", "n": 100, "n_null": 0,
                     "n_unique": 50, "stats": {}, "extras": {}, "alerts": [],
                     "null_rate": 0.0}],
    }))
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
    # WCAG: skip-link + main landmark required
    assert 'href="#main"' in body
    assert '<main' in body and 'id="main"' in body


def test_unknown_findings_returns_404(client):
    resp = client.get("/view/does-not-exist")
    assert resp.status_code == 404
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `pytest tests/test_viewer_app.py -v`
Expected: FAIL — module not found.

- [ ] **Step 2.3: Write minimal implementation**

```python
# saturn/viewer/app.py
"""Flask app factory for the saturn live viewer."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, render_template, request

from .loader import FindingsDoc, FindingsKind, list_findings, load_findings

DEFAULT_PORT = 5043


def create_app(*, findings_dir: Path, testing: bool = False) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["SATURN_FINDINGS_DIR"] = Path(findings_dir)
    app.config["TESTING"] = testing

    @app.get("/")
    def index():
        docs = list_findings(app.config["SATURN_FINDINGS_DIR"])
        return render_template("index.html.j2", docs=docs)

    @app.get("/view/<id>")
    def view(id: str):
        path = app.config["SATURN_FINDINGS_DIR"] / f"{id}.json"
        if not path.is_file():
            abort(404)
        doc = load_findings(path)
        if doc.kind is FindingsKind.PROFILE:
            return render_template("profile.html.j2", doc=doc)
        return render_template("compare.html.j2", doc=doc)

    @app.get("/api/findings/<id>")
    def api_findings(id: str):
        path = app.config["SATURN_FINDINGS_DIR"] / f"{id}.json"
        if not path.is_file():
            abort(404)
        doc = load_findings(path)
        return doc.raw

    @app.get("/health")
    def health():
        return {"status": "ok", "findings_dir": str(app.config["SATURN_FINDINGS_DIR"])}

    return app
```

```html+jinja
{# saturn/viewer/templates/base.html.j2 #}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{% block title %}saturn{% endblock %}</title>
  <style>
    :root { color-scheme: light dark; }
    *,*::before,*::after { box-sizing: border-box; }
    body { font: 16px/1.5 system-ui, sans-serif; margin: 0; color: #111; background: #fff; }
    @media (prefers-color-scheme: dark) { body { color: #eee; background: #111; } }
    .skip-link { position: absolute; top: -40px; left: 0; background: #000; color: #fff; padding: 8px; }
    .skip-link:focus { top: 0; }
    main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
    header.site { border-bottom: 1px solid currentColor; padding: 0.75rem 1.5rem; }
    :focus-visible { outline: 3px solid #06c; outline-offset: 2px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { padding: 0.4rem 0.6rem; border-bottom: 1px solid #ccc; text-align: left; }
    .badge { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.85em; }
  </style>
  {% block head_extra %}{% endblock %}
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <header class="site"><strong>saturn</strong> — live viewer</header>
  <main id="main" tabindex="-1">
    {% block main %}{% endblock %}
  </main>
</body>
</html>
```

```html+jinja
{# saturn/viewer/templates/index.html.j2 #}
{% extends "base.html.j2" %}
{% block title %}saturn — findings index{% endblock %}
{% block main %}
<h1>Findings</h1>
{% if docs %}
<table>
  <caption class="visually-hidden">All saturn findings on disk</caption>
  <thead><tr><th scope="col">Name</th><th scope="col">Kind</th><th scope="col">Source / Labels</th><th scope="col">Open</th></tr></thead>
  <tbody>
  {% for d in docs %}
    <tr>
      <td>{{ d.id }}</td>
      <td><span class="badge">{{ d.kind.value }}</span></td>
      <td>
        {% if d.kind.value == "profile" %}{{ d.meta.source }}
        {% else %}{{ d.a_label }} vs {{ d.b_label }}{% endif %}
      </td>
      <td><a href="{{ url_for('view', id=d.id) }}">open</a></td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p>No findings files in <code>{{ config.SATURN_FINDINGS_DIR }}</code>.
   Run <code>saturn analyze …</code> and drop the resulting JSON in that directory.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `pytest tests/test_viewer_app.py -v`
Expected: 3 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add saturn/viewer/app.py saturn/viewer/templates/ tests/test_viewer_app.py
git commit -m "feat: viewer flask app factory + index route"
```

---

## Task 3: Profile view (single-dataset)

**Files:**
- Create: `saturn/viewer/templates/profile.html.j2`
- Test: extend `tests/test_viewer_app.py`

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_viewer_app.py`:

```python
def test_profile_view_renders_columns(client, findings_dir):
    resp = client.get("/view/demo")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "hf://demo/set" in body
    assert "alt_text" in body or "column" in body.lower()
    # WCAG: table caption + scoped headers
    assert "scope=\"col\"" in body


def test_profile_view_exposes_json_via_api(client):
    resp = client.get("/api/findings/demo")
    assert resp.status_code == 200
    assert resp.is_json
    data = resp.get_json()
    assert data["meta"]["source"] == "hf://demo/set"
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `pytest tests/test_viewer_app.py::test_profile_view_renders_columns -v`
Expected: FAIL — profile template missing.

- [ ] **Step 3.3: Write `profile.html.j2`**

```html+jinja
{% extends "base.html.j2" %}
{% block title %}{{ doc.id }} — saturn{% endblock %}
{% block main %}
<nav aria-label="Breadcrumb"><a href="{{ url_for('index') }}">← all findings</a></nav>
<h1>{{ doc.meta.source }}</h1>
<p class="muted">
  <strong>{{ "{:,}".format(doc.meta.row_count or 0) }}</strong> rows,
  <strong>{{ doc.columns | length }}</strong> columns,
  generated {{ doc.meta.generated_at }}.
</p>

<section aria-labelledby="schema-heading">
  <h2 id="schema-heading">Schema</h2>
  <table>
    <caption class="visually-hidden">Per-column summary</caption>
    <thead>
      <tr>
        <th scope="col"><button type="button" data-sort="column">Column</button></th>
        <th scope="col"><button type="button" data-sort="kind">Kind</button></th>
        <th scope="col"><button type="button" data-sort="null_rate">Null %</button></th>
        <th scope="col"><button type="button" data-sort="n_unique">Unique</button></th>
        <th scope="col">Alerts</th>
      </tr>
    </thead>
    <tbody id="columns-tbody">
    {% for c in doc.columns %}
      <tr data-column="{{ c.column }}">
        <th scope="row"><a href="#col-{{ c.column }}">{{ c.column }}</a></th>
        <td>{{ c.kind }}</td>
        <td>{{ "%.1f%%" | format((c.null_rate or 0) * 100) }}</td>
        <td>{{ c.n_unique if c.n_unique is not none else "—" }}</td>
        <td>
          {% for a in c.alerts %}
            <span class="badge alert-{{ a.level }}" title="{{ a.message }}">{{ a.code }}</span>
          {% endfor %}
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</section>

{% for c in doc.columns %}
<section id="col-{{ c.column }}" aria-labelledby="col-{{ c.column }}-heading">
  <h3 id="col-{{ c.column }}-heading">{{ c.column }} <small class="muted">({{ c.kind }})</small></h3>
  <dl>
    <dt>n</dt><dd>{{ c.n }}</dd>
    <dt>nulls</dt><dd>{{ c.n_null }} ({{ "%.1f%%" | format((c.null_rate or 0) * 100) }})</dd>
    <dt>unique</dt><dd>{{ c.n_unique if c.n_unique is not none else "—" }}</dd>
    {% for k, v in c.stats.items() %}
      <dt>{{ k }}</dt><dd>{{ v }}</dd>
    {% endfor %}
  </dl>
</section>
{% endfor %}

<script src="{{ url_for('static', filename='sort.js') }}" defer></script>
{% endblock %}
```

- [ ] **Step 3.4: Create `saturn/viewer/static/sort.js`** (minimal, progressive enhancement)

```javascript
// saturn/viewer/static/sort.js
(() => {
  const tbody = document.getElementById('columns-tbody');
  if (!tbody) return;
  const numericCols = new Set(['null_rate', 'n_unique']);

  document.querySelectorAll('button[data-sort]').forEach(btn => {
    let asc = true;
    btn.addEventListener('click', () => {
      const key = btn.dataset.sort;
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {
        const ka = a.dataset.column;
        const kb = b.dataset.column;
        const cellA = key === 'column' ? ka : a.cells[{column:0,kind:1,null_rate:2,n_unique:3}[key]].textContent.trim();
        const cellB = key === 'column' ? kb : b.cells[{column:0,kind:1,null_rate:2,n_unique:3}[key]].textContent.trim();
        if (numericCols.has(key)) {
          const na = parseFloat(cellA); const nb = parseFloat(cellB);
          return asc ? na - nb : nb - na;
        }
        return asc ? cellA.localeCompare(cellB) : cellB.localeCompare(cellA);
      });
      rows.forEach(r => tbody.appendChild(r));
      asc = !asc;
      btn.setAttribute('aria-pressed', String(!asc));
    });
  });
})();
```

- [ ] **Step 3.5: Run test to verify it passes**

Run: `pytest tests/test_viewer_app.py -v`
Expected: all pass.

- [ ] **Step 3.6: Commit**

```bash
git add saturn/viewer/templates/profile.html.j2 saturn/viewer/static/sort.js tests/test_viewer_app.py
git commit -m "feat: profile view with sortable schema table and deep-links"
```

---

## Task 4: Compare view

**Files:**
- Create: `saturn/viewer/templates/compare.html.j2`
- Test: extend `tests/test_viewer_app.py`

- [ ] **Step 4.1: Write the failing test**

```python
def test_compare_view_renders_both_sides(client, tmp_path, findings_dir):
    # write a compare findings file into the same dir
    import json
    (findings_dir / "diff.json").write_text(json.dumps({
        "saturn_version": "0.1.0",
        "a": {"label": "curated", "source": "hf://x/y[curated]", "row_count": 279000,
              "schema": {"alt_text": "text"}, "language_counts": {}},
        "b": {"label": "firehose", "source": "hf://x/y[firehose]", "row_count": 125000,
              "schema": {"alt_text": "text"}, "language_counts": {}},
        "columns": [{"column": "alt_text", "kind": "text",
                     "a": {"column": "alt_text", "kind": "text", "n": 279000,
                           "n_null": 0, "n_unique": 250000, "stats": {}, "extras": {},
                           "alerts": [], "null_rate": 0.0},
                     "b": {"column": "alt_text", "kind": "text", "n": 125000,
                           "n_null": 0, "n_unique": 120000, "stats": {}, "extras": {},
                           "alerts": [], "null_rate": 0.0},
                     "delta": {"len_mean_delta": 79.0}, "notes": []}],
        "divergences": [{"column": "alt_text", "kind": "text", "score": 0.82,
                         "signals": ["len_mean +79"]}],
        "generated_at": "2026-04-22T00:00:00+00:00",
    }))

    resp = client.get("/view/diff")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "curated" in body and "firehose" in body
    assert "+79" in body or "79" in body
    assert "279,000" in body or "279000" in body
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `pytest tests/test_viewer_app.py::test_compare_view_renders_both_sides -v`
Expected: FAIL — compare template missing.

- [ ] **Step 4.3: Write `compare.html.j2`**

```html+jinja
{% extends "base.html.j2" %}
{% block title %}{{ doc.a_label }} vs {{ doc.b_label }} — saturn{% endblock %}
{% block main %}
<nav aria-label="Breadcrumb"><a href="{{ url_for('index') }}">← all findings</a></nav>
<h1>{{ doc.a_label }} <small>vs</small> {{ doc.b_label }}</h1>
<p class="muted">
  <strong>{{ doc.a_label }}:</strong> {{ "{:,}".format(doc.raw.a.row_count) }} rows &middot;
  <strong>{{ doc.b_label }}:</strong> {{ "{:,}".format(doc.raw.b.row_count) }} rows &middot;
  generated {{ doc.raw.generated_at }}
</p>

{% if doc.raw.divergences %}
<section aria-labelledby="div-heading">
  <h2 id="div-heading">Most divergent columns</h2>
  <table>
    <caption class="visually-hidden">Columns ranked by divergence score</caption>
    <thead><tr><th scope="col">Column</th><th scope="col">Kind</th><th scope="col">Score</th><th scope="col">Signals</th></tr></thead>
    <tbody>
    {% for d in doc.raw.divergences %}
      <tr>
        <th scope="row"><a href="#col-{{ d.column }}">{{ d.column }}</a></th>
        <td>{{ d.kind }}</td>
        <td>{{ "%.2f" | format(d.score) }}</td>
        <td>{{ d.signals | join(", ") }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}

{% for c in doc.raw.columns %}
<section id="col-{{ c.column }}" aria-labelledby="col-{{ c.column }}-heading">
  <h3 id="col-{{ c.column }}-heading">{{ c.column }} <small class="muted">({{ c.kind }})</small></h3>
  <table>
    <caption class="visually-hidden">Side-by-side stats for {{ c.column }}</caption>
    <thead><tr><th scope="col">Stat</th><th scope="col">{{ doc.a_label }}</th><th scope="col">{{ doc.b_label }}</th><th scope="col">Δ</th></tr></thead>
    <tbody>
      <tr><th scope="row">n</th>
          <td>{{ c.a.n if c.a else "—" }}</td>
          <td>{{ c.b.n if c.b else "—" }}</td>
          <td>—</td></tr>
      <tr><th scope="row">null rate</th>
          <td>{{ "%.1f%%" | format((c.a.null_rate or 0) * 100) if c.a else "—" }}</td>
          <td>{{ "%.1f%%" | format((c.b.null_rate or 0) * 100) if c.b else "—" }}</td>
          <td>{% if c.delta.null_rate_delta is defined %}{{ "%+.1f%%" | format(c.delta.null_rate_delta * 100) }}{% else %}—{% endif %}</td></tr>
      {% for k, v in c.delta.items() if k.endswith("_delta") and k != "null_rate_delta" %}
      <tr><th scope="row">{{ k }}</th><td>—</td><td>—</td><td>{{ v }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endfor %}
{% endblock %}
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `pytest tests/test_viewer_app.py -v`
Expected: all pass.

- [ ] **Step 4.5: Commit**

```bash
git add saturn/viewer/templates/compare.html.j2 tests/test_viewer_app.py
git commit -m "feat: compare view with divergence summary and side-by-side tables"
```

---

## Task 5: CLI `saturn serve` command

**Files:**
- Modify: `saturn/cli.py`
- Test: `tests/test_viewer_cli.py`

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_viewer_cli.py
from unittest.mock import patch

from typer.testing import CliRunner

from saturn.cli import app


def test_serve_command_help_lists_port_and_dir():
    runner = CliRunner()
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--dir" in result.output


def test_serve_command_invokes_app_run(tmp_path):
    runner = CliRunner()
    with patch("saturn.cli.create_app") as mock_create:
        fake_app = mock_create.return_value
        fake_app.run.return_value = None
        result = runner.invoke(app, ["serve", "--dir", str(tmp_path), "--port", "5043"])
    assert result.exit_code == 0, result.output
    mock_create.assert_called_once()
    fake_app.run.assert_called_once()
    kwargs = fake_app.run.call_args.kwargs
    assert kwargs["port"] == 5043
    assert kwargs["host"] == "127.0.0.1"
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `pytest tests/test_viewer_cli.py -v`
Expected: FAIL — `serve` command missing.

- [ ] **Step 5.3: Modify `saturn/cli.py`**

Add imports:

```python
try:
    from .viewer.app import DEFAULT_PORT, create_app
except ImportError:      # [web] extra not installed
    create_app = None
    DEFAULT_PORT = 5043
```

Add command:

```python
@app.command(name="serve", help="Start the live viewer (requires pip install 'saturn-dissect[web]').")
def serve(
    directory: Path = typer.Option(Path.cwd(), "--dir", help="directory containing findings JSON files"),
    port: int = typer.Option(DEFAULT_PORT, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    if create_app is None:
        console.print("[red]viewer not installed. run: pip install 'saturn-dissect[web]'")
        raise typer.Exit(code=1)
    if not directory.is_dir():
        console.print(f"[red]not a directory:[/] {directory}")
        raise typer.Exit(code=2)
    flask_app = create_app(findings_dir=directory)
    console.print(f"[green]saturn viewer[/] on [bold]http://{host}:{port}[/] serving [dim]{directory}[/]")
    flask_app.run(host=host, port=port, debug=debug)
```

- [ ] **Step 5.4: Run test to verify it passes**

Run: `pytest tests/test_viewer_cli.py -v`
Expected: 2 PASS.

- [ ] **Step 5.5: Commit**

```bash
git add saturn/cli.py tests/test_viewer_cli.py
git commit -m "feat: 'saturn serve' cli command"
```

---

## Task 6: Accessibility smoke test

**Files:**
- Create: `tests/test_viewer_a11y.py`

**Why:** saturn's product contract says WCAG 2.2 AA. Axe / pa11y are out-of-scope for CI (no browser), but we can catch the most common structural regressions with HTML parsing.

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_viewer_a11y.py
import json
import re
from pathlib import Path

import pytest

from saturn.viewer.app import create_app


@pytest.fixture
def client(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps({
        "saturn_version": "0.1.0",
        "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0, "mode": "full",
                 "generated_at": "2026-04-22T00:00:00+00:00"},
        "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
        "columns": [{"column": "a", "kind": "numeric", "n": 1, "n_null": 0, "n_unique": 1,
                     "stats": {}, "extras": {}, "alerts": [], "null_rate": 0.0}],
    }))
    return create_app(findings_dir=tmp_path, testing=True).test_client()


def _fetch(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    return resp.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/", "/view/one"])
def test_pages_have_lang_attr(client, path):
    html = _fetch(client, path)
    assert re.search(r"<html[^>]+lang=", html), "root <html> must carry a lang attribute"


@pytest.mark.parametrize("path", ["/", "/view/one"])
def test_pages_have_single_h1(client, path):
    html = _fetch(client, path)
    assert len(re.findall(r"<h1[>\s]", html)) == 1


@pytest.mark.parametrize("path", ["/", "/view/one"])
def test_tables_have_captions_or_aria_label(client, path):
    html = _fetch(client, path)
    tables = re.findall(r"<table[^>]*>.*?</table>", html, flags=re.DOTALL)
    for t in tables:
        assert "<caption" in t or "aria-label" in t, "every table needs a caption or aria-label"


@pytest.mark.parametrize("path", ["/", "/view/one"])
def test_has_skip_link_and_main_landmark(client, path):
    html = _fetch(client, path)
    assert 'href="#main"' in html
    assert re.search(r"<main[^>]*id=\"main\"", html)
```

- [ ] **Step 6.2: Run**

Run: `pytest tests/test_viewer_a11y.py -v`
Expected: PASS against the templates written in Tasks 2–4.

- [ ] **Step 6.3: Commit**

```bash
git add tests/test_viewer_a11y.py
git commit -m "test: viewer wcag 2.2 aa structural guards"
```

---

## Task 7: Service manager + Caddy integration

**Files (outside this repo — document here, do NOT attempt to auto-edit):**
- Document: `docs/DEPLOY.md`

- [ ] **Step 7.1: Write `docs/DEPLOY.md`**

```markdown
# Deploying saturn viewer on dr.eamer.dev

## 1. Register with `sm`

Append to `~/service_manager.py` `SERVICES` dict:

    'saturn-viewer': {
        'name': 'Saturn Viewer',
        'script': '/home/coolhand/projects/saturn/saturn/scripts/start.sh',
        'working_dir': '/home/coolhand/projects/saturn/saturn',
        'port': 5043,
        'health_endpoint': 'http://localhost:5043/health',
        'start_timeout': 15,
        'description': 'Live findings viewer (WCAG 2.2 AA)',
    }

## 2. `scripts/start.sh`

    #!/usr/bin/env bash
    set -euo pipefail
    source venv/bin/activate
    export PYTHONPATH=/home/coolhand/shared:$PYTHONPATH
    exec gunicorn --bind 127.0.0.1:5043 \
                  --workers 2 --threads 4 \
                  'saturn.viewer.app:create_app(findings_dir="/home/coolhand/saturn-findings")'

Make executable: `chmod +x scripts/start.sh`. Then `pip install -e '.[web]' gunicorn`.

## 3. Caddy route

Path-stripped pattern (React-style; viewer assumes mount at root):

    handle_path /saturn/* { reverse_proxy localhost:5043 }

The viewer uses `url_for` for every internal link; reverse-proxy path-strip works without further config.

## 4. Verify

    sm start saturn-viewer
    sm logs saturn-viewer
    curl -s https://dr.eamer.dev/saturn/health
```

- [ ] **Step 7.2: Commit**

```bash
git add docs/DEPLOY.md
git commit -m "docs: deploy saturn viewer on dr.eamer.dev (sm + caddy)"
```

*Note:* service-manager and Caddy changes were intentionally kept outside this application implementation plan because they coordinate shared server infrastructure.

---

## Task 8: Manual smoke test + README

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 8.1: Manual smoke test**

```bash
source venv/bin/activate
export PYTHONPATH=/home/coolhand/shared:$PYTHONPATH
saturn analyze tests/fixtures/tiny.csv --findings /tmp/saturn-live/demo.json --out /tmp/saturn-live/demo.html
mkdir -p /tmp/saturn-live
saturn serve --dir /tmp/saturn-live --port 5043
```

Open `http://127.0.0.1:5043`. Verify:
- index shows `demo`
- clicking `open` renders the profile page
- Tab key navigates through the skip link, then into the sort buttons
- `/api/findings/demo` returns JSON

- [ ] **Step 8.2: Update README**

Add a **Viewer** section between **Use** and **Output**:

```markdown
## Viewer

```bash
pip install -e '.[web]'
saturn serve --dir ./findings-dir --port 5043
open http://127.0.0.1:5043
```

A live, WCAG 2.2 AA–compliant alternative to the static HTML report. Reads any saturn findings JSON in the directory.
```

- [ ] **Step 8.3: Flip CLAUDE.md roadmap** — Phase 5 → ✅.

- [ ] **Step 8.4: Commit + merge**

```bash
git add README.md CLAUDE.md
git commit -m "docs: phase 5 viewer shipped"
git checkout main
git merge --no-ff phase5-flask-viewer
```

---

## Self-review notes

- **Spec coverage:** "Flask viewer on port 5043 reading the JSON findings file" → Task 2 (app + index), Task 3 (profile view), Task 4 (compare view), Task 5 (CLI). ✓
- **No placeholders:** every step has concrete code or commands.
- **Type consistency:** `FindingsDoc`, `FindingsKind.PROFILE/COMPARE`, `create_app(findings_dir=, testing=)`, `DEFAULT_PORT = 5043` stable from Task 1 onward.
- **WCAG coverage:** Task 2 base template has skip-link + `<main>` + focus styles + color-scheme. Task 6 locks structural invariants (lang, single h1, table captions, landmark).
- **Interop with Phase 2:** the viewer's JSON API returns `doc.raw` verbatim, so once Phase 2 lands and findings include `"insights"` the API serves them with zero changes. The templates can gain a `{% if doc.raw.insights %}` section in a follow-up slice without disturbing this plan.

## Non-goals (do not implement here)

- WebSocket / SSE live updates. Findings files are static artifacts; add SSE only when Phase 2 gains streaming insight generation.
- Authentication. Viewer listens on `127.0.0.1` by default; public exposure goes through Caddy + existing `dr.eamer.dev` auth patterns.
- Editing findings. Viewer is read-only. Regenerate via `saturn analyze …` and reload.
- D3 / rich interactive charts. Scope is tables + deep links + sort. Phase 5.5 can add Plotly rendering once the viewer's UX shape is validated in use.
- Batch comparison UI (three+ findings side by side). Out of scope — use pairwise `saturn compare` and view the result.
