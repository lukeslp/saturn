# saturn

Dataset dissector. Point it at a HuggingFace repo or a local CSV / JSONL / Parquet / SQLite file and it produces a terminal summary, a self-contained HTML report, and a machine-readable JSON findings file. Stats pass is always free and deterministic. Language-model insight and topic clustering are opt-in.

Built generic-first with alt-text research as the first real consumer — specifically [lukeslp/bluesky-alt-text](https://huggingface.co/datasets/lukeslp/bluesky-alt-text) (404,841 image descriptions).

## Install

```bash
python3.10 -m venv venv
source venv/bin/activate
pip install -e '.[nlp,llm,dev]'
python -m spacy download en_core_web_sm
python -m spacy download xx_ent_wiki_sm   # multilingual fallback
```

Python 3.10+ required. If you are outside the dreamer host, make sure `~/shared` is on `PYTHONPATH` or install provider clients directly via `pip install -e '.[llm]'`.

## Use

```bash
# Stats-only pass against a HuggingFace dataset (free, fast, deterministic)
saturn huggingface lukeslp/bluesky-alt-text

# Local file, custom output paths
saturn analyze /path/to/posts.parquet --out report.html --findings findings.json

# Add language-model commentary (sample 2000 rows, hard 50-cent cap)
saturn analyze lukeslp/bluesky-alt-text --llm --provider groq --max-cost-usd 0.50

# Catfish pattern: a second provider argues with the first
saturn analyze lukeslp/bluesky-alt-text --llm --provider groq --critic anthropic

# Add short-text topic clustering (BERTopic, slower)
saturn analyze lukeslp/bluesky-alt-text --cluster
```

Output:

1. **Terminal** — row count, per-column type, null %, distribution hints, notable alerts
2. **HTML** — one self-contained file, TOC, per-column charts and stats, optional cluster + insight sections
3. **JSON findings** — every number and string in the HTML, ready to feed a notebook generator or a later web UI

## Design

- Two passes: a free deterministic stats pass, an opt-in language-model insight pass
- Catfish, not chorus: default to one strong provider; `--critic X` adds a single adversarial second opinion
- Streaming ingestion (`datasets` streaming, DuckDB scan) so million-row corpora do not hit memory limits
- `--sample N` reservoir sampling (default 2000) for the expensive passes
- Cost guard via `--max-cost-usd` (default 0.50) — the language-model pass refuses to exceed it
- Plugin profilers: each column type gets its own `BaseProfiler`. Add a profiler, register it, it runs

## Status

Phase 1 (stats + HTML report + JSON findings) shipping. Phases 2–5 (language-model insight, BERTopic clustering, alt-text-specific comparisons, optional Flask viewer on port 5043) on the roadmap.

## License

MIT. © Luke Steuber.
