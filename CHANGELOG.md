# Changelog

All notable changes to saturn are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project tracks [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **GeoJSON ingestion.** `.geojson` FeatureCollections flatten to one row per feature (the union of `properties` keys plus a synthetic `geometry_type`); coordinate geometry is dropped as non-tabular. Previously `.geojson` raised "Unsupported file type".
- **Robust JSON arrays with heterogeneous keys.** `.json` files whose objects carry different key sets (a late record with an extra field) now load via full-scan inference with a Python-union fallback, and `schema()` falls back to the eager loader when DuckDB can't cheaply sample. Fixes "set union_by_name to true" / "extra field in struct" failures on real 50k–350k-row arrays.
- **Wide-table guard on the per-column LLM pass.** `analyze` now skips per-column insight calls for near-empty columns (`null_rate >= --llm-skip-null-rate`, default 0.95) and caps the rest at `--llm-max-columns` (default 40), prioritising informative columns. A pathologically wide file (a real CSV had 257 columns, ~237 of them empty `Unnamed: N` from an Excel export) no longer fires one provider call per column and times out. Deterministic stats still cover every column; `--llm-max-columns -1` restores the uncapped behaviour.
- **Accessible standalone report.** The CLI `--out` HTML now pairs every Plotly figure with a `<details>Show data table</details>` companion (overview null-rate, correlation, language, and per-column charts), reusing the same `chart_fallback` builders as the live viewer. Previously this WCAG 2.2 AA fallback only rendered in the viewer path.
- **`--no-evidence-values` redaction flag** (also `SATURN_REDACT_EVIDENCE_VALUES=1`) on `analyze` and `huggingface`. Withholds the literal cell values (`top_values` / `top_words`) from the model-ready evidence; counts, stats, and language mix still go to the provider. For datasets carrying PII/PHI under HIPAA/GDPR/FERPA.
- **Per-value byte cap (200 bytes)** on `top_values` / `top_words` forwarded to the LLM. Stops one long-text column from blowing the provider context window (a real holdout projected to 1.67M tokens). Short values keep their original tuple shape.
- **fastText `lid.176` attribution.** Reports whose language counts came from fastText now carry a CC-BY-SA-3.0 notice in the HTML footer and an `attributions` key in the JSON findings sidecar (single-dataset and compare paths). Reports that fell back to langdetect carry no such obligation.
- **`LICENSE` (MIT) + `NOTICE`** in the repo root. NOTICE groups every dependency by license (Apache-2.0 / BSD / MIT / MPL-2.0 / PSF), flags the disjunctively-licensed `pyphen`, and documents the fastText `lid.176` CC-BY-SA-3.0 model asset.
- **README "Data handling" section** naming exactly what leaves the machine on `--llm`, which providers receive it, and how to redact.
- **Per-finding annotations.** Drop a `<id>.notes.md` sidecar next to the findings JSON and the viewer renders it inline above the LLM reading on both the report view (as `.notes-card`) and the notebook view (as a `.cell-notes` cell). Markdown goes through `markdown` + `bleach` with a research-tuned allowlist (tables, fenced code, footnotes, links). Script, style, iframe, inline handlers, and `javascript:` URLs are stripped. Bare URLs auto-linkify with `rel="nofollow noopener"`. Index gains a `notes` stamp + "with notes" filter. Tests: 19 covering missing/empty/corrupt files, XSS payloads, end-to-end render.
- **Insight error hint.** When a previous LLM pass failed, the missing-summary form surfaces a redacted plain-language hint above the "Generate summary" button. Recognises four common patterns: credit balance exhausted, 429 rate limit, auth/invalid key, timeout. Anything else falls through to a generic "previous attempt failed (TYPE)". The raw provider error message stays on disk for debugging but never reaches the rendered HTML. Tests: 10 covering each error type + raw-message redaction.
- **Notebook view (`?view=notebook`).** Cell gutters (`[n]:` / `Out[n]:` / `Fig n.`), inline Plotly figures, reproducibility footer. Toggle between report and notebook on every finding page.
- **`.ipynb` export.** `/view/<id>.ipynb` returns valid nbformat 5.x with markdown + matplotlib code cells, one plot cell per column. Cell IDs match nbformat 5.x compat requirements.
- **Citation block.** BibTeX + APA on every finding page, click-to-copy via the `data-copy` attribute.
- **Style guide.** `/styleguide` documents the datasheet-annual aesthetic (Fraunces serif + IBM Plex Mono, warm bone paper, signal red accent), with copy-pasteable markup for chips, alerts, stamps, summary cards, drop zones, notebook cells, footers, and the notes sidecar.
- **Searchable + paginated index** for 200+ findings: live filter on slug + source, kind/reading/notes radios, `/`-key focus shortcut, 50-per-page pagination, `data-has-insight` + `data-has-notes` attributes.
- **Collections rail** auto-derived from slug prefix on the index (chips appear when ≥3 findings share a prefix).
- **Multi-sheet workbooks.** `--sheet NAME_OR_INDEX` for XLSX/XLS/XLSB/ODS. `list_sheets()` introspects available sheet names. Saturn warns when a workbook has multiple sheets and a specific one wasn't picked.
- **A11y data-table fallback.** Every Plotly figure has a `<details>Show data table</details>` companion with a screen-reader-friendly `<table>`. Covers numeric histograms, categorical top-values (with shares), text length histograms, top-words, language counts (engine keys stripped), correlation matrices (capped at 12×12), and per-column null rates.
- **LLM-curated columns (prompt v2).** Per-column `role` (`identifier` / `feature` / `free_text` / `quasi_identifier` / `noise`) and `treatment` (one-line guidance: "drop", "encode", "tokenize as text", etc.). Dataset-level `featured_charts` lets the model pick 3–5 columns with custom captions for a featured rail above per-column sections. Prompt schema bumped from `saturn-insight-v1` to `saturn-insight-v2`.
- **BYOK on public viewer.** Per-request API key field on `/analyze` and `/analyze-hf`. Anonymous uploads default to stats-only when no key is provided. Server keys are scrubbed from the subprocess env when no BYOK is supplied. The provider dropdown now lists anthropic, openai, groq, gemini, mistral, cohere, xai, perplexity, huggingface, and ollama. Picking ollama with no key threads a `"local"` sentinel through the resolver, runner, and key loader so saturn talks to `http://localhost:11434` keyless. Setting `OLLAMA_API_KEY=local` is explicitly avoided since unauthenticated localhost ollama rejects a literal `Bearer local` header.
- **Demo mode.** Server defaults to `anthropic:claude-opus-4-7` and forms ship with the LLM pass enabled. BYOK is now an optional override rather than the only path.
- **`/batch` progress page** for live monitoring of bulk runs.
- **Spreadsheet, TSV, Feather/Arrow ingestion** via the polars direct path.
- 89 new tests since 0.2.0. Total now 290 passing.

### Changed
- README peer-comparison table (saturn vs. ydata-profiling vs. sweetviz vs. dataprep) and live-demo link at the top.
- Index card surfaces column count and the LLM-tagged role mix as chips.

### Fixed
- **Findings written before charts render.** `_emit_outputs` now writes the JSON findings sidecar first, then attempts the HTML. A chart-rendering crash (e.g. all-NaN coordinates on `glottolog_coordinates`) degrades to a warning instead of discarding the deterministic stats pass.
- `extract_json` does a balanced-brace scan so Opus's habit of writing prose after the JSON object stops breaking insight pass.
- `pyjson5` fallback handles malformed Opus output (single quotes, trailing commas, unquoted keys).
- SQLite ingestion: `DETACH DATABASE IF EXISTS s` before `ATTACH` so re-loads don't fail. Empty-table picker prefers row-count-largest, skipping `sqlite_sequence`. Switched from `sqlite_master` (DuckDB-specific quirk) to `information_schema.tables WHERE table_catalog = 's'`.
- `request.url_root` no longer doubles the prefix on cite URLs (use `url_for(... _external=True)` instead).
- Caddy `/saturn` (no trailing slash) used to hit the static archive; added `redir /saturn /saturn/ 308` and removed the legacy archive at `~/html/saturn`.
- `nbformat 5.x` MissingIDFieldWarning: every cell now has a unique `id`.
- LLM gateway: serialise concurrent provider calls via a semaphore, exponential backoff on 429s, single gunicorn worker on the deployed viewer.
- Strip dead `hotspots` field from prompt evidence (was redundant with the role mix surfaced in v2).
- `X-Forwarded-Prefix` is honored only when `SATURN_TRUST_FORWARDED_PREFIX=1`, so `url_for` prepends `/saturn` correctly behind Caddy without trusting arbitrary upstreams.
- Latent typecheck error: `viewer/app.py` referenced `Any` without importing it (worked at runtime because Python doesn't evaluate local variable annotations).

### Internal
- Eight elegance refactors applied across profilers/cli/compare/ingestion/report: `_emit_outputs` helper, `_emit_common_alerts` registry, `_DIVERGENCE_TERMS` table, `_ensure_frame` helper, `_delta_rows` kind dispatch, single `_run` with `sample_size` switch, `_numeric_stats` shared between polars + dict paths.
- `BYOK` form fields unified via the `byok_fields` macro; both inline forms on the index now use it.
- `insight_error_hint` macro moved into `_macros.html.j2` so it renders identically across report and notebook views.
- `chart_fallback` helpers in `viewer/chart_fallback.py`: `column_data_table`, `overview_data_table`, `language_data_table`, `correlation_data_table`.
- `notes.py` for sidecar markdown rendering. `loader.py` propagates `has_notes` so the index can filter without re-reading every file.

## [0.2.0] - 2026-04-22

### Added
- **Phase 5 live viewer.** `saturn serve --dir <findings-dir> --port 5043` starts a Flask app that reads any saturn findings JSON file off disk and renders an interactive profile or compare view. Shipped behind a `[web]` optional dependency (`pip install 'saturn-dissect[web]'`). Routes: `/`, `/view/<id>`, `/api/findings/<id>`, `/health`. WCAG 2.2 AA structural guards are enforced in the test suite.
- **Phase 2 LLM insight pass.** `saturn analyze --llm provider[:model]` layers a narrated insight pass on top of the deterministic stats. Pass `--llm` twice and the second provider plays catfish critic, reviewing the first model's narrative against the same evidence. Insights land in both the HTML report and the JSON findings under the `insights` key. The pass fails open: provider errors or missing API keys never block the deterministic output. Supported providers (via `~/shared/llm_providers`): anthropic, openai, groq, gemini, mistral, cohere, xai, perplexity, huggingface, ollama.
- **Phase 2.5 compare-mode insight pass.** `saturn compare --llm` walks the top-K most divergent columns (ranked by the same composite score used in the "most divergent" section) and emits pair-aware narratives that refer to each side by its label. Delta-aware prompts highlight the strongest divergence first and call out low language/top-value jaccards explicitly.
- `InsightBundle.to_dict()` and `ReportData.insight_bundle` optional field. The `insights` key only appears in findings JSON when a bundle is present, preserving backwards compatibility.
- `saturn version` command.
- `docs/DEPLOY.md`: `sm` and Caddy wiring notes for the viewer.
- `docs/superpowers/plans/`: two implementation plans used to drive this release.
- 82 new tests (total now 93 passing).

### Changed
- `saturn/cli.py` gained two commands (`serve`, `version`) and a `--llm` option on `analyze` and `huggingface`.
- README: reorganised around shipped phases; no more speculative copy.

### Internal
- New package `saturn.llm/` with five focused modules (`evidence`, `prompts`, `gateway`, `parsing`, `engine`). The `gateway` is the sole entry point to any model; no direct vendor SDK imports appear anywhere else in the codebase.
- New package `saturn.viewer/` with a Flask app factory (`create_app`), Jinja templates, and a progressive-enhancement sort helper.

## [0.1.0] - 2026-04-21

Initial release.

### Added
- Polars-native full-corpus profiling by default, with opt-in `--sample N` for streaming mode.
- `saturn analyze` for local files (CSV, JSONL, Parquet, SQLite), `saturn huggingface` for HF repos, `saturn compare` for pairwise diffs.
- Self-contained HTML report with Plotly charts and a JSON findings sidecar.
- Alert surface: `duplicates`, `high_skew`, `outliers`, `multilingual`, `near_unique`, `boilerplate`, `allcaps`, `one_word`, `url_heavy`.
- Compare mode with composite divergence score and streaming-fallback load for HF datasets with divergent shard schemas.
- Optional fasttext `lid.176` language detection at ~1M docs/sec on the full corpus; falls back to a bounded `langdetect` sample.
