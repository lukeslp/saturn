# Changelog

All notable changes to saturn are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project tracks [Semantic Versioning](https://semver.org/).

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
