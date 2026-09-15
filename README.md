# saturn

Dataset dissector. Point it at a HuggingFace repo, a local file, or a slice of either and it produces a terminal summary, a self-contained HTML report, and a machine-readable JSON findings file. The statistics pass runs without a provider account. Language-model interpretation is optional; topic clustering is planned.

Use it to inspect column types, missing values, repeated values, distributions, and differences between datasets.

**Example reports:** [dr.eamer.dev/saturn](https://dr.eamer.dev/saturn).
The repository's deployment notes describe that public path as a static archive,
separate from the upload-capable viewer. Run your own viewer for interactive
analysis; see [deployment boundaries](docs/DEPLOY.md).

## Workbench direction

The recommended product direction extends Saturn into a native macOS research
workbench with a menu-bar Drop Shelf, local and remote dataset connectors, and
modular Papers, Data, and Media workspaces. The current CLI and viewer ship
today. Two validation spikes gate the desktop work. See the canonical
[Saturn Workbench Plan](docs/product/SATURN_WORKBENCH.md).

## Install from source

Python 3.10 or later is required by the package metadata. From a checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
saturn version
```

The base install supports profiling and comparison. Add `.[web]` for the viewer,
`.[llm]` for provider-backed interpretation, or `.[dev,web]` for the test suite.
The `[nlp]` extra installs heavier optional libraries; installing it does not
add the planned topic-clustering workflow.

For the optional fastText language detector, install `.[nlp]` and download the
separately licensed model:

```bash
mkdir -p .cache/saturn
curl -sL -o .cache/saturn/lid.176.bin https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
```

Without that model, language detection uses a bounded `langdetect` sample.

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

Start a local viewer with model interpretation disabled by default:

```bash
pip install -e '.[web]'
SATURN_DEFAULT_LLM='' saturn serve --dir path/to/findings/ --port 5043
open http://127.0.0.1:5043
```

The viewer reads findings from the selected directory. It also includes upload,
Hugging Face analysis, notebook export, and model interpretation routes. Anyone
who can reach an exposed viewer may be able to see its findings; localhost is
the default binding. Read [docs/DEPLOY.md](docs/DEPLOY.md) before exposing it.

To enable provider calls, install `.[llm]`, select the provider, and configure
credentials outside the repository. Without an override, the viewer selects
`openai:gpt-5.6-luna`; an upload can request that model without a CLI `--llm`
flag. The form's stats-only option disables the model stage for that request.
Provider failures are recorded while deterministic analysis continues.

The viewer has structural accessibility tests for landmarks, controls, and
chart data tables. Those checks do not establish full WCAG conformance.

## Design

- **Polars-native, full-corpus by default.** Use `--sample N` when loading the whole dataset would be too expensive.
- **Bounded memory.** `duplicate_counter` and vocab expansion get skipped on JSON-blob-shaped columns; near-unique columns skip value-counts that would return 400K singletons; vocab tokenisation is capped to a 20K-row subsample truncated to 500 chars per row.
- **fastText lid.176** for language detection when the model is present. Falls back to a bounded `langdetect` sample otherwise.
- **Two passes.** A free deterministic stats pass (always runs) and an opt-in language-model insight pass (Phase 2, shipped).
- **Schema inference** uses absolute *and* relative cardinality: a 489-value column in a 404K-row corpus is categorical, not text, even though 489 > the absolute threshold.
- **Compare mode.** Diff two slices column-by-column; every delta (null drift, mean/length delta, entropy delta, top-value jaccard, language-mix jaccard) is both visible in the HTML and machine-readable in the JSON.

## Historical example

A previously recorded run of `saturn compare lukeslp/bluesky-alt-text --by source_mode` used 404,841 rows and took 12 to 18 seconds. These are historical observations, not a benchmark for the current release or other machines:

| signal | curated (279K) | firehose (125K) | Δ |
|---|---|---|---|
| `alt_text` mean length | 202 chars | 281 chars | **+79 chars** |
| `alt_text` duplicate rate | 8.5% | 20.0% | +11.5pp |
| language jaccard | . | . | **0.35** (wide divergence) |
| `author_handle` null | 0% | 100% | **+100%** (firehose is anonymised) |
| `cursor` duplicate rate | 75% | 17% | **−58pp** |

In that run, the curated 489-account sample had shorter descriptions on average than the broader stream. This comparison alone does not establish a difference in readability.

## LLM insight pass (opt-in)

Pass `--llm provider[:model]` on `analyze`, `huggingface`, or `compare` to layer a narrated insight pass on top of the deterministic stats. Pass the flag twice and the second provider plays catfish critic: it reviews the first model's narrative against the same evidence and returns `agree`/`disagree`/`partial`. Insights land in both the HTML report and the JSON findings (key: `insights`). The pass fails open: provider errors or missing API keys never block the deterministic output; they are recorded in `insights.errors` and saturn exits 0.

On compare mode the insight pass walks the top-K most divergent columns (ranked by the same composite score powering the "most divergent" section) and emits a pair-aware narrative referring to each side by its label.

Install `pip install 'saturn-dissect[llm]'` and set the provider's standard API-key environment variable. Supported providers: anthropic, openai, groq, gemini, mistral, cohere, xai, perplexity, huggingface, ollama.

## Data handling

For a local file analyzed with the CLI and no `--llm`, profiling runs on your
machine. A Hugging Face source requires network access to retrieve data. The
optional language-model stage sends evidence to the selected provider. A web
upload transfers the file to the machine hosting the viewer, whose provider
settings are separate from CLI defaults.

When the insight pass runs, saturn sends a compact per-column projection to the provider you chose. That projection is the same surface a reader already sees in the JSON sidecar: row counts, null rates, distinct counts, numeric stats, the language mix, and by default the column's most frequent values and words. Saturn never sends raw rows, and it never sends a column's full contents. Long literal values are truncated to a 200-byte prefix plus an ellipsis.

Those top values and words are still literal cell contents, so on a dataset with names, handles, free text, or anything else sensitive (PII/PHI under HIPAA, GDPR, or FERPA), they can carry identifying data. Two ways to withhold them:

```bash
# per run: send only aggregates, no literal cell values
saturn analyze data.csv --llm anthropic --no-evidence-values

# deployment-wide: force redaction without threading a flag through every form
export SATURN_REDACT_EVIDENCE_VALUES=1
```

With redaction on, counts, stats, and the language mix still go to the model; literal values and words are held back. Column names and source identifiers remain in the evidence and may also be sensitive.

The destination is the provider selected by the CLI flag or viewer settings. Ollama normally uses a local endpoint, but a configured remote endpoint changes that boundary.

Language detection runs on the machine performing the analysis, including the server when using a hosted viewer. When the fastText `lid.176` model is present, saturn uses it and stamps a CC-BY-SA-3.0 attribution into the report footer and the JSON sidecar (`attributions` key), to preserve model provenance. The model license does not, by itself, establish that every derived statistic inherits that license. See [NOTICE](NOTICE).

## Implemented features

- Stats pass (Phase 1): full-corpus polars profiling, HTML + JSON output
- LLM insight pass (Phase 2): `--llm provider[:model]`, catfish critic on a second `--llm`, all commands
- Compare-mode insights (Phase 2.5): pair evidence, delta-aware prompts
- Compare mode (Phase 4): composite divergence score, streaming fallback
- Flask viewer (Phase 5): `saturn serve --port 5043`, drop-zone upload, HF analysis, structural accessibility tests
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

Third-party dependency attributions are in [NOTICE](NOTICE). The optional fastText `lid.176` model is distributed under CC BY-SA 3.0. Saturn includes model attribution in affected reports and JSON findings. That attribution does not change the license of your input dataset.

## Tests

```bash
pip install -e '.[dev,web]'
SATURN_NO_NETWORK=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 pytest
```

The suite uses synthetic rows when no local alt-text fixture is present and
mocks provider calls. It checks software behavior, not the accuracy of an
external dataset or a complete accessibility certification.
