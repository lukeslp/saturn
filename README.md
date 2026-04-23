# saturn

Dataset dissector. Point it at a HuggingFace repo, a local file, or a slice of either and it produces a terminal summary, a self-contained HTML report, and a machine-readable JSON findings file. Stats pass is always free and deterministic. Language-model insight and topic clustering are opt-in.

Generic across domains: alt-text, Bluesky firehose, census tables, VQA annotations all work out of the box.

Primary use case: [lukeslp/bluesky-alt-text](https://huggingface.co/datasets/lukeslp/bluesky-alt-text), 404,841 image descriptions, profiled in 46 s, compared across the curated/firehose split in 18 s.

## Install

```bash
python3.10 -m venv venv
source venv/bin/activate
pip install -e '.[nlp]'
# optional, enables true full-corpus language detection:
mkdir -p .cache/saturn
curl -sL -o .cache/saturn/lid.176.bin https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
```

Python 3.10+ required.

## Use

```bash
# Full-corpus profile of a HuggingFace dataset
saturn huggingface lukeslp/bluesky-alt-text

# Local file (CSV, TSV, JSONL, JSON, Parquet, Feather/Arrow, XLSX/XLS/XLSB/ODS, SQLite)
saturn analyze path/to/data.csv

# Compare two slices of one dataset by a column value
saturn compare lukeslp/bluesky-alt-text --by source_mode \
    --label-a curated --label-b firehose

# Compare two independent sources
saturn compare hf://user/a hf://user/b

# Opt-in streaming sample when full load is too heavy
saturn analyze big-dataset.parquet --sample 5000

# Opt-in LLM insight pass on any command (primary, with optional catfish critic)
saturn analyze data.csv --llm anthropic
saturn analyze data.csv --llm anthropic:claude-sonnet-4-6 --llm openai:gpt-4o-mini
saturn compare data.csv --by slice --llm anthropic
```

## Output

1. **Terminal**: row count, per-column type, null %, unique count, alerts (`duplicates`, `high_skew`, `outliers`, `multilingual`, `near_unique`, `boilerplate`, `allcaps`, `one_word`, `url_heavy`, and so on).
2. **HTML report**: one self-contained file, TOC with per-column charts and stats tables. Compare mode adds a "most divergent columns" summary driven by a composite score.
3. **JSON findings**: every number and string in the HTML, ready to feed a notebook generator or the live viewer.

## Viewer

```bash
pip install -e '.[web]'
saturn serve --dir path/to/findings/ --port 5043
open http://127.0.0.1:5043
```

A WCAG 2.2 AA compliant live alternative to the static HTML report. Drop findings JSON files into a directory; refreshing the index picks up new runs without restarting. `/api/findings/<id>` returns the raw JSON for scripting.

## Design

- **Polars-native, full-corpus by default.** 404K rows of 21-column Bluesky data profiled in under a minute. Opt-in `--sample N` for quick peeks on anything bigger.
- **Bounded memory.** `duplicate_counter` and vocab expansion get skipped on JSON-blob-shaped columns; near-unique columns skip value-counts that would return 400K singletons; vocab tokenisation is capped to a 20K-row subsample truncated to 500 chars per row.
- **fasttext lid.176** for true full-corpus language detection when the model is present (~1M docs/sec). Falls back to a bounded `langdetect` sample otherwise.
- **Two passes.** A free deterministic stats pass (always runs) and an opt-in language-model insight pass (Phase 2, shipped).
- **Schema inference** uses absolute *and* relative cardinality: a 489-value column in a 404K-row corpus is categorical, not text, even though 489 > the absolute threshold.
- **Compare mode** is the dataset's feature. Diff two slices column-by-column; every delta (null drift, mean/length delta, entropy delta, top-value jaccard, language-mix jaccard) is both visible in the HTML and machine-readable in the JSON.

## Real output

Running `saturn compare lukeslp/bluesky-alt-text --by source_mode` on the full 404,841-row corpus (12 to 18 s):

| signal | curated (279K) | firehose (125K) | Δ |
|---|---|---|---|
| `alt_text` mean length | 202 chars | 281 chars | **+79 chars** |
| `alt_text` duplicate rate | 8.5% | 20.0% | +11.5pp |
| language jaccard | . | . | **0.35** (wide divergence) |
| `author_handle` null | 0% | 100% | **+100%** (firehose is anonymised) |
| `cursor` duplicate rate | 75% | 17% | **−58pp** |

The **+79 chars** on firehose vs curated was the non-obvious finding: the curated 489-account population writes *shorter* alt text than the broader stream. Worth a Concadia-style readability follow-up.

## LLM insight pass (opt-in)

Pass `--llm provider[:model]` on `analyze`, `huggingface`, or `compare` to layer a narrated insight pass on top of the deterministic stats. Pass the flag twice and the second provider plays catfish critic: it reviews the first model's narrative against the same evidence and returns `agree`/`disagree`/`partial`. Insights land in both the HTML report and the JSON findings (key: `insights`). The pass fails open: provider errors or missing API keys never block the deterministic output; they are recorded in `insights.errors` and saturn exits 0.

On compare mode the insight pass walks the top-K most divergent columns (ranked by the same composite score powering the "most divergent" section) and emits a pair-aware narrative referring to each side by its label.

Requires `~/shared/llm_providers` on `PYTHONPATH` (the unified provider gateway). Supported providers: anthropic, openai, groq, gemini, mistral, cohere, xai, perplexity, huggingface, ollama.

## Status

Phase 1 (stats + HTML + JSON), Phase 2 (`--llm` insight pass with catfish critic, all commands), Phase 2.5 (compare-mode insights with pair evidence and delta-aware prompts), Phase 4 (compare mode, curated-vs-firehose diff), and Phase 5 (Flask viewer on port 5043) are all shipping. Phase 3 (BERTopic clustering) on the roadmap.

## License

MIT. © Luke Steuber.
