# saturn

Dataset dissector. Point it at a HuggingFace repo, a local file, or a slice of either and it produces a terminal summary, a self-contained HTML report, and a machine-readable JSON findings file. Stats pass is always free and deterministic. Language-model insight and topic clustering are opt-in.

Generic across domains: alt-text, Bluesky firehose, census tables, VQA annotations all work out of the box.

**Live demo:** [dr.eamer.dev/saturn](https://dr.eamer.dev/saturn). Drop a CSV/Parquet/XLSX file or paste a HuggingFace repo id, get a notebook-style reading with a plain-language summary, role-tagged columns, and downloadable `.ipynb`.

Primary use case: [lukeslp/bluesky-alt-text](https://huggingface.co/datasets/lukeslp/bluesky-alt-text), 404,841 image descriptions, profiled in 46 s, compared across the curated/firehose split in 18 s.

## How saturn differs from other profilers

| | saturn | ydata-profiling | sweetviz | dataprep |
|---|---|---|---|---|
| Default scan | **full corpus** (polars-native) | sample with cap | full corpus (pandas) | full corpus |
| Bounded memory on wide text | **yes** (vocab caps, near-unique skip) | partial | partial | partial |
| Compare mode | **pairwise + composite divergence score** | pairwise overlay | pairwise (its specialty) | no |
| LLM-narrated reading | **yes, opt-in, catfish-critic** | no | no | no |
| Per-column LLM role + treatment | **yes** | no | no | no |
| JSON sidecar matching the HTML | **yes** | partial | no | no |
| Notebook view (`?view=notebook`) | **yes** | no | no | no |
| Real `.ipynb` export | **yes** (`/view/<id>.ipynb`) | no | no | no |
| Live web viewer with upload form | **yes** (Flask, port 5043) | no | no | no |
| WCAG 2.2 AA structural guards | **yes** (tested) | no | no | no |
| Multilingual at scale | **yes** (fasttext lid.176, ~1M docs/s) | basic | basic | basic |

The bold cells are the columns where saturn was built specifically. The deterministic stats pass is always free; the LLM pass is one extra flag and is the differentiator if you've ever asked yourself "what *is* this dataset, plain English."

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

### Local machine helper

`saturn helper JOB.json` is the stable, newline-delimited interface for local
desktop integrations. The job and every referenced path must share one
directory; relative paths cannot escape it. A version 1 profile job is:

```json
{
  "jobVersion": 1,
  "operation": "profile",
  "inputs": [{
    "path": "input.arrow",
    "format": "arrow",
    "descriptor": {"source": "accepted-citations"}
  }],
  "outputPath": "result.json",
  "options": {"seed": 42}
}
```

Use two inputs and `"operation": "compare"` for pairwise analysis; each
descriptor may add a `label`. JSON inputs are arrays of record objects. Arrow
inputs are IPC files. The helper writes exactly one contract-v1 JSON artifact
with atomic replacement. Standard output contains JSON progress events only,
including a stable error or cancellation phase on failure. Interrupted and
failed runs remove temporary files and never partially replace an artifact.

## Output

1. **Terminal**: row count, per-column type, null %, unique count, alerts (`duplicates`, `high_skew`, `outliers`, `multilingual`, `near_unique`, `boilerplate`, `allcaps`, `one_word`, `url_heavy`, and so on).
2. **HTML report**: one self-contained file, TOC with per-column charts and stats tables. Compare mode adds a "most divergent columns" summary driven by a composite score.
3. **JSON findings**: every number and string in the HTML, ready to feed a notebook generator or the live viewer.

## Viewer

```bash
pip install -e '.[web,llm]'
export OPENAI_API_KEY='...'  # required for the default Luna workflow
saturn serve --dir path/to/findings/ --port 5043
open http://127.0.0.1:5043
```

A WCAG 2.2 AA compliant live alternative to the static HTML report. Drop findings JSON files into a directory; refreshing the index picks up new runs without restarting. `/api/findings/<id>` returns the raw JSON for scripting.

Inject provider credentials through the service environment; never store them in the repository. Without `OPENAI_API_KEY`, the default `openai:gpt-5.6-luna` stage fails open and Saturn still returns the deterministic analysis without a model narrative.

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

Install `pip install 'saturn-dissect[llm]'` and set the provider's standard API-key environment variable. Supported providers: anthropic, openai, groq, gemini, mistral, cohere, xai, perplexity, huggingface, ollama.

## Data handling

The stats pass is entirely local. Nothing leaves your machine unless you pass `--llm`.

When the insight pass runs, saturn sends a compact per-column projection to the provider you chose. That projection is the same surface a reader already sees in the JSON sidecar: row counts, null rates, distinct counts, numeric stats, the language mix, and by default the column's most frequent values and words. Saturn never sends raw rows, and it never sends a column's full contents. Each forwarded value is truncated to 200 bytes so one long-text column cannot balloon the request.

Those top values and words are still literal cell contents, so on a dataset with names, handles, free text, or anything else sensitive (PII/PHI under HIPAA, GDPR, or FERPA), they can carry identifying data. Two ways to withhold them:

```bash
# per run: send only aggregates, no literal cell values
saturn analyze data.csv --llm anthropic --no-evidence-values

# deployment-wide: force redaction without threading a flag through every form
export SATURN_REDACT_EVIDENCE_VALUES=1
```

With redaction on, counts, stats, and the language mix still go to the model; only the verbatim values and words are held back.

The destination is whichever provider you name in `--llm`: anthropic, openai, groq, gemini, mistral, cohere, xai, perplexity, huggingface, or a local ollama (which keeps everything on `localhost:11434`).

Language detection is local in every mode. When the fastText `lid.176` model is present, saturn uses it and stamps a CC-BY-SA-3.0 attribution into the report footer and the JSON sidecar (`attributions` key), because language counts produced by `lid.176` are a derivative work. See [NOTICE](NOTICE).

## Status

All shipping:
- Stats pass (Phase 1): full-corpus polars profiling, HTML + JSON output
- LLM insight pass (Phase 2): `--llm provider[:model]`, catfish critic on a second `--llm`, all commands
- Compare-mode insights (Phase 2.5): pair evidence, delta-aware prompts
- Compare mode (Phase 4): composite divergence score, streaming fallback
- Flask viewer (Phase 5): `saturn serve --port 5043`, drop-zone upload, HF analysis, WCAG 2.2 AA
- Notebook view: `?view=notebook` toggle with cell gutters + inline plots
- `.ipynb` export: `/view/<id>.ipynb` returns valid nbformat, matplotlib plots per column
- LLM-curated columns (prompt v2): per-column `role` and `treatment` chips, dataset-level `featured_charts`
- Multi-sheet workbooks: `--sheet NAME_OR_INDEX` for XLSX/XLS/XLSB/ODS, warns when a workbook has multiple sheets
- A11y data-table fallback: every Plotly chart has a `<details>Show data table</details>` companion for screen readers
- Citation block: BibTeX + APA on every finding, click to copy
- Per-finding annotations: drop a `<id>.notes.md` next to the JSON, viewer renders it (markdown sanitised)
- Style guide: `/styleguide` reference with copy-pasteable markup

On the roadmap: Phase 3 (BERTopic clustering via `[nlp]` extra), SSE progress for long analyze-hf jobs.

## License

MIT. © Luke Steuber. Full text in [LICENSE](LICENSE).

Third-party dependency attributions are in [NOTICE](NOTICE). One runtime asset carries its own terms: the optional fastText `lid.176` language model is CC-BY-SA-3.0, so any report whose language counts came from it is a derivative work for those figures and carries that license (stamped into the report footer and the JSON `attributions` key).
