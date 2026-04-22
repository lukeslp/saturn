# saturn

Dataset dissector. Point it at a HuggingFace repo, a local file, or a slice of either and it produces a terminal summary, a self-contained HTML report, and a machine-readable JSON findings file. Stats pass is always free and deterministic. Language-model insight and topic clustering are opt-in (roadmap).

Generic across domains — alt-text, Bluesky firehose, census tables, VQA annotations all work out of the box.

Primary use case: [lukeslp/bluesky-alt-text](https://huggingface.co/datasets/lukeslp/bluesky-alt-text) — 404,841 image descriptions — profiled in 46 s, compared across the curated/firehose split in 18 s.

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

# Local file (CSV, JSONL, Parquet, SQLite)
saturn analyze path/to/data.csv

# Compare two slices of one dataset by a column value
saturn compare lukeslp/bluesky-alt-text --by source_mode \
    --label-a curated --label-b firehose

# Compare two independent sources
saturn compare hf://user/a hf://user/b

# Opt-in streaming sample when full load is too heavy
saturn analyze big-dataset.parquet --sample 5000
```

## Output

1. **Terminal** — row count, per-column type, null %, unique count, alerts (`duplicates`, `high_skew`, `outliers`, `multilingual`, `near_unique`, `boilerplate`, `allcaps`, `one_word`, `url_heavy` …)
2. **HTML report** — one self-contained file, TOC + per-column charts + stats tables + (in compare mode) a "most divergent columns" summary driven by a composite score
3. **JSON findings** — every number and string in the HTML, ready to feed a notebook generator or a later web UI

## Design

- **Polars-native, full-corpus by default.** 404K rows of 21-column Bluesky data profiled in under a minute. Opt-in `--sample N` for quick peeks on anything bigger.
- **Bounded memory.** `duplicate_counter` / vocab expansion get skipped on JSON-blob-shaped columns; near-unique columns skip value-counts that would return 400K singletons; vocab tokenisation is capped to a 20K-row subsample truncated to 500 chars per row.
- **fasttext lid.176** for true full-corpus language detection when the model is present (~1M docs/sec). Falls back to a bounded `langdetect` sample otherwise.
- **Two passes.** A free deterministic stats pass (always runs) and an opt-in language-model insight pass (roadmap, Phase 2).
- **Schema inference** uses absolute *and* relative cardinality — a 489-value column in a 404K-row corpus is categorical, not text, even though 489 > the absolute threshold.
- **Compare mode** is the dataset's feature. Diff two slices column-by-column; every delta — null drift, mean/length delta, entropy delta, top-value jaccard, language-mix jaccard — is both visible in the HTML and machine-readable in the JSON.

## Real output

Running `saturn compare lukeslp/bluesky-alt-text --by source_mode` on the full 404,841-row corpus (12–18 s):

| signal | curated (279K) | firehose (125K) | Δ |
|---|---|---|---|
| `alt_text` mean length | 202 chars | 281 chars | **+79 chars** |
| `alt_text` duplicate rate | 8.5% | 20.0% | +11.5pp |
| language jaccard | — | — | **0.35** (wide divergence) |
| `author_handle` null | 0% | 100% | **+100%** (firehose is anonymised) |
| `cursor` duplicate rate | 75% | 17% | **−58pp** |

The **+79 chars** on firehose vs curated was the non-obvious finding — the curated 489-account population writes *shorter* alt text than the broader stream. Worth a Concadia-style readability follow-up.

## Status

Phase 1 (stats + HTML + JSON) and Phase 4 (compare mode, curated-vs-firehose diff) shipping. Phase 2 (`--llm` insight pass with catfish critic), Phase 3 (BERTopic clustering), Phase 5 (Flask viewer on port 5043) on the roadmap.

## License

MIT. © Luke Steuber.
