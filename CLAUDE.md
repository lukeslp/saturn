# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**saturn** (PyPI name: `saturn-dissect`, CLI: `saturn`) is a dataset dissector. Point it at a HuggingFace repo or a local tabular file (CSV, TSV, JSON/JSONL, Parquet, Feather/Arrow, XLSX/XLS/XLSB/ODS, SQLite) and it emits three artifacts from one run: a Rich terminal summary, a self-contained HTML report, and a JSON findings sidecar. The stats pass is free and deterministic; the language-model insight pass (Phase 2) and BERTopic clustering (Phase 3) are opt-in. Primary target dataset: `lukeslp/bluesky-alt-text` (404K rows, profiled in ~46 s).

This directory is an **independent git repo** (remote: `lukeslp/saturn`). It is listed in the parent `projects/` gitignore, so `git status` at the parent shows nothing. Always `cd` here and run git commands locally.

## Commands

```bash
# One-time setup
python3.10 -m venv venv
source venv/bin/activate
pip install -e '.[nlp,dev]'

# Full corpus profile
saturn huggingface lukeslp/bluesky-alt-text
saturn analyze path/to/data.csv

# Sampled mode (opt-in for very large datasets)
saturn analyze big.parquet --sample 5000

# Compare two slices of one dataset
saturn compare lukeslp/bluesky-alt-text --by source_mode \
    --label-a curated --label-b firehose

# Compare two independent sources
saturn compare hf://user/a hf://user/b

# LLM insight pass (opt-in; PYTHONPATH must include ~/shared)
saturn analyze data.csv --llm anthropic
saturn analyze data.csv --llm anthropic:claude-sonnet-4-6 --llm openai:gpt-4o-mini

# Live viewer (opt-in; pip install -e '.[web]')
saturn serve --dir ./findings --port 5043

# Testing
pytest                                        # all tests
pytest tests/test_profilers.py -v             # one file
pytest tests/test_compare.py::test_name -v    # one test
SATURN_NO_NETWORK=1 pytest                    # skip HF fixture download, use synthetic

# Language detection upgrade (optional but recommended)
mkdir -p .cache/saturn
curl -sL -o .cache/saturn/lid.176.bin https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
```

## Architecture

Five modules form a one-way pipeline: **ingest → profile → (compare) → render → (viewer/insights)**. Each layer owns one dataclass contract the next layer consumes, so swapping any layer is a local change.

### `saturn/ingestion.py`: SourceAdapter hierarchy

`adapter_for(source)` dispatches on the string: `hf://…` or `user/repo` routes to `HFAdapter`, everything else to `FileAdapter`. Both implement the same four methods:

- `schema()`: cheap sample-based type inference (numeric/text/categorical/boolean/unknown). For HF this streams the first 100 rows; for local files DuckDB reads 200 rows.
- `load_dataframe()`: the **default path**. Full corpus into a polars DataFrame.
- `iter_batches(batch_size)`: streaming fallback, used only by `reservoir_sample` in `--sample N` mode.
- `row_count()`: cheap, prefers dataset metadata over counting rows.

Two schema inference paths: `_infer_schema_from_sample` (dict-based, streaming) and `_schema_from_dataframe` (polars-native, uses both absolute cardinality and unique-ratio, so it catches 489-distinct-author columns in 400K rows as categorical even though 489 > absolute threshold).

**HF adapter quirks to know:**
- `split=None` means "concat every split", a deliberate default for a whole-corpus tool.
- `load_dataframe` has a streaming fallback for `DatasetGenerationError` (divergent shard schemas) that caps at 500K rows, unions keys across shards, and stringifies nested dicts that confuse polars.

### `saturn/profilers.py`: two entry points, one result type

`profile_dataframe(df, schema)` is the default (vectorised, full corpus). `profile_columns(schema, sample)` is the list-of-dict path used by unit tests and by `--sample N`. Both return `list[ProfileResult]` with identical shape.

**Memory bounds matter here** (the single largest source of crashes on wide corpora):
- Text columns skip `duplicate_counter` and vocab expansion when they look like JSON blobs (>80% start with `{` or `[`).
- Near-unique columns (unique_ratio > 0.95) skip `value_counts`, since a 400K-row `cid` column would return 400K singletons.
- Vocabulary tokenisation is capped to a 20K-row subsample, each row truncated to 500 chars.
- Language detection uses fasttext `lid.176` when `.cache/saturn/lid.176.bin` exists (~1M docs/sec, full corpus); falls back to `langdetect` on a 5K-row bounded sample.

Constants governing this live at the top of `profilers.py` (`_TOP_VALUES_K`, `_LANG_SAMPLE_K`, `_CARD_WARN`, etc.). Tune there before threading options through the CLI.

### `saturn/compare.py`: pairwise diff

`compare_dataframes(df_a, df_b, …)` profiles both sides independently, then walks the union of columns producing one `ColumnComparison` per column. Delta keys are contractual, since the report template reads them by name:
- `null_rate_delta`, `mean_delta`, `len_mean_delta`, `entropy_delta`
- `top_value_jaccard`, `language_jaccard` (lower = more divergent)

`CompareReport.divergence_summary(k=6)` ranks the top-K most divergent columns with a capped-term composite score. Every term is clamped to ≤1.0 so one runaway value cannot dominate the ranking.

`split_dataframe(df, by, values)` powers `saturn compare --by COL`: partitions one frame by a column's top-2 values (or the explicit `--values a,b`), returns `[(label, sub_df), …]`.

### `saturn/report.py`, `saturn/charts.py`, `saturn/templates/`

`assemble(…)` returns `ReportData` (the full contract). `render_html(data, path)` feeds it to Jinja2 templates `report.html.j2` and `compare.html.j2`. Plotly produces inlined `<div>` fragments via `chart_for()` dispatch (numeric histogram, categorical bar, text length/vocab, language donut, correlation heatmap, dataset overview).

**Self-contained HTML is load-bearing**: no external JS/CSS requests. The JSON findings sidecar (`to_findings()`) mirrors every number in the HTML so a downstream notebook generator or the Flask viewer can reconstruct the report without parsing HTML.

### `saturn/cli.py`: Typer app

Four commands: `analyze`, `huggingface`, `compare`, `serve`, plus `version`. `_run_full` and `_run_sampled` are the two profile paths; the `--sample` option is the only switch between them. `_maybe_run_insights(data, llm_spec)` layers the optional LLM pass after profiling, before render. Rich tables print the summary before the HTML writes, so even an aborted run shows something useful.

### `saturn/llm/`: opt-in insight pass (Phase 2, shipped)

Engaged only when `--llm provider[:model]` is passed. Five small modules:
- `evidence.py`: projects `ReportData` into a compact model-ready dict. The pass never sees raw user rows.
- `prompts.py`: deterministic system/user prompt builders, version-tagged `saturn-insight-v1`.
- `gateway.py`: single choke point over `~/shared/llm_providers.ProviderFactory`. No direct vendor SDK imports allowed anywhere else.
- `parsing.py`: tolerant JSON extraction plus schema validation.
- `engine.py`: orchestrates primary insight + optional catfish critique with fail-open error handling.

`ReportData.insight_bundle` is populated before `to_findings()` serialises; the `insights` key only appears in the JSON when a bundle exists. See `docs/superpowers/plans/2026-04-22-phase2-llm-insight-pass.md` for the full task ladder.

### `saturn/viewer/`: live Flask viewer (Phase 5, shipped)

Reads JSON findings off disk and serves them via Flask on port 5043. Routes: `/`, `/view/<id>`, `/api/findings/<id>`, `/health`. WCAG 2.2 AA: skip-link, `<main>` landmark, single `<h1>` per page, every table has a caption, sort UI is real `<button>` not styled anchor. The API route returns `doc.raw` verbatim, so Phase 2 insights surface there automatically. Deploy notes in `docs/DEPLOY.md`.

## Testing conventions

- `tests/conftest.py` caches a 500-row `lukeslp/bluesky-alt-text` slice to `tests/fixtures/` on first run; subsequent runs are offline. `SATURN_NO_NETWORK=1` forces the synthetic fallback (seeded, 200 rows, multilingual).
- `asyncio_mode = "auto"` in `pyproject.toml`. Do not decorate async tests.
- Tests import the lightweight path: `profile_columns(schema, sample)` on a list of dicts. Prefer this over constructing polars frames in tests unless the code under test is polars-specific (see `test_dataframe_profile.py`).
- LLM tests mock `saturn.llm.engine.call_provider` at the module boundary. No tests make real API calls. Live replay fixtures are a future addition, gated on `SATURN_REPLAY_LLM`.

## Design invariants (do not break casually)

- **Polars is the data spine.** Pandas is only used when a third-party API hands us one (HF `.to_pandas()` fallback, DuckDB `.fetch_df()`). Convert as soon as possible.
- **Full corpus by default; sample is opt-in.** This is the product's differentiator from pandas-profiling and ydata-profiling. Any change that silently subsamples is a regression.
- **Stats pass stays free and deterministic.** LLM calls (Phase 2) and GPU-backed clustering (Phase 3) must go behind flags, must not gate the default output, and must never mutate the base `ProfileResult` contract.
- **One result dataclass, two producers.** `profile_dataframe` (polars) and `profile_columns` (dicts) must stay drop-in interchangeable for the same `(df, schema)`. When adding a metric, add it to both paths or document why it is vectorised-only.
- **HTML report is self-contained.** No CDN links, no external fonts. One file, double-clickable. (The viewer runs live and may reach for CDN assets; the static report must not.)
- **LLM gateway is the only path to a model.** No direct `anthropic`, `openai`, `groq` imports anywhere outside `saturn/llm/gateway.py`.

## Roadmap (shipping vs. planned)

- **Phase 1** (shipped): stats pass, HTML, JSON
- **Phase 2** (shipped): `--llm provider[:model]` insight pass with catfish critic, analyze and huggingface commands
- **Phase 2.5** (shipped): compare-mode insight pass with pair evidence and delta-aware prompts
- **Phase 4** (shipped): compare mode, divergence summary, streaming fallback
- **Phase 5** (shipped): Flask viewer on port 5043 (`saturn serve`), read-only, WCAG 2.2 AA
- **Phase 3** (planned): BERTopic clustering via the `[nlp]` extra

The `ReportData.to_findings()` contract is the interchange format everything downstream keys off. The viewer serves it verbatim via `/api/findings/<id>`, so Phase 2 insights appear in the viewer automatically once the key is populated.

## Known gotchas

- **System twine is broken** on this box. For PyPI publish, use `~/build-venv/` (Python 3.10 venv with working twine).
- **Python 3.10 required.** No 3.11/3.12 support on this server.
- **HF bulk load sometimes fails** with `DatasetGenerationError` on datasets whose shards disagree on schema. The streaming fallback in `HFAdapter._load_dataframe_via_stream` recovers most of these but caps at 500K rows.
- **Saturn outputs are gitignored** at the repo level (`saturn_report.html`, `saturn_findings.json`, `*.saturn.{html,json}`). Generated reports never get committed accidentally.
- **`--llm` needs `~/shared` on `PYTHONPATH`.** Without it the flag prints an error and the deterministic pass still runs. The CI suite sets `PYTHONPATH` so tests can import the gateway.
