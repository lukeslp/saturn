# Research Workbench Implementation Plan

> Implementation uses test-first repair slices, an independent review after
> each task, and explicit commits/checkpoints.

**Goal:** Turn Saturn into the reliable portable analysis engine for a
project-based Citewrite research and visualization workbench.

**Architecture:** Stabilize Saturn's deterministic truth first, then introduce a
versioned core/job contract. Citewrite consumes that contract through a local
helper before document synthesis, table extraction, and reviewed Gallery-derived
rendering are layered on top.

**Tech stack:** Python 3.10+, Polars/Arrow, JSON Schema, Swift 6, SwiftData,
SwiftUI, WKWebView, bundled D3 renderer assets.

## Global constraints

- Deterministic Saturn profiling and comparison remain free, model-independent,
  and full-corpus by default.
- Contract JSON contains no NaN or infinity.
- Redaction removes every literal cell value from model evidence.
- Citewrite remains local-first; hosted document synthesis is explicit per run.
- Only reviewed, bundled visualization templates execute in Citewrite.
- Existing CLI/viewer workflows remain compatible while contract v1 lands.

---

### Task 1: Numeric validity and aligned correlations

**Files:** `saturn/profilers.py`, `saturn/report.py`, relevant profiler/report
tests.

- Add failing tests for NaN/infinity accounting and mismatched-null correlation.
- Normalize non-finite values before numeric statistics and serialize finite
  results only.
- Compute correlations from a shared deterministic DataFrame sample with
  pairwise-complete rows and pair counts; stop aligning per-column compacted
  samples.
- Run focused tests, full offline tests, self-review, commit, and task review.

### Task 2: Schema-safe comparison and complete redaction

**Files:** `saturn/compare.py`, `saturn/llm/evidence.py`,
`saturn/llm/compare_evidence.py`, comparison/evidence tests.

- Add failing tests for numeric-to-text schema drift and redacted comparison
  deltas.
- Profile each side with its own schema; record side kinds and compatibility.
- Compute deltas only for compatible kinds and rank schema drift explicitly.
- Centralize literal projection so explicit redaction covers side and delta data.
- Run focused/full tests, commit, and task review.

### Task 3: Safe ingestion, atomic artifacts, and notebook escaping

**Files:** ingestion, report serialization, viewer runner/notebook, focused tests.

- Add failing path/table quoting and generated-code injection tests.
- Parameterize or correctly quote DuckDB literals and SQLite identifiers.
- Add strict finite JSON serialization and atomic temp/fsync/replace writes.
- Emit notebook literals with safe Python quoting.
- Run focused/full tests, commit, and task review.

### Task 4: Viewer resource lifecycle

**Files:** viewer app/runner, deployment configuration, viewer tests.

- Add failing tests for upload cleanup on success/error/timeout and bounded job
  retention.
- Introduce a bounded executor/queue and active-job cap.
- Add parsed dataset row/column/cell safeguards.
- Add upload/job/result TTL cleanup suitable for the testing deployment.
- Keep authentication and multi-tenant policy out of scope.
- Run focused/full tests, commit, and task review.

### Task 5: Packaging, CI, documentation, and Saturn release

**Files:** `pyproject.toml`, provider boundary, CI workflow, deployment/readme/
changelog/version files.

- Make clean `[web,dev]` installation collect and test without manual packages.
- Make `[llm]` usable without a private `PYTHONPATH` dependency.
- Include Gunicorn and annotation-rendering dependencies in the correct extras.
- Add Python 3.10/3.11 CI, package-build/install smoke, CLI smoke, and offline tests.
- Correct mutation-route and worker/thread deployment documentation.
- Bump one authoritative version, update changelog, build artifacts, commit, and
  review.

### Task 6: Saturn core contract v1

**Files:** new dependency-light `saturn/core/` domain/serialization modules,
compatibility adapter, JSON Schema, golden fixtures and tests.

- Define dataset descriptors, typed column profiles, aligned correlations with
  pair counts, schema-safe comparisons, provenance, options, and extensions.
- Add `contractVersion: 1` and artifact kinds for profile/comparison.
- Preserve unknown extension fields and migrate existing 0.2 findings.
- Keep Flask/Jinja/Plotly/provider imports outside the core.
- Validate deterministic golden artifacts and commit/review.

### Task 7: Local machine helper

**Files:** helper command, job-envelope types, Arrow adapter, helper tests/docs.

- Accept JSON job envelopes for profile/compare with Arrow IPC or small JSON
  inputs, isolated output paths, options, progress, cancellation, and structured
  errors.
- Write one contract-v1 artifact atomically.
- Prove CLI/helper/viewer semantic equivalence on golden input.
- Commit and review.

### Task 8: Citewrite projects and collection analysis

**Repository:** citation-renamer, on its own feature worktree and checkpoint.

- Add ResearchProject, collection membership, dataset/version, analysis run, and
  visualization-spec persistence models with migration-safe defaults.
- Add Workbench navigation and project management.
- Add Analyze actions for accepted citations, explicit selections, and batches.
- Convert citation metadata to the helper contract and render completeness,
  author/year/venue/type/identifier/duplicate and comparison results natively.
- Link aggregates back to citations and surface stale/re-run/helper-error states.
- Add Swift golden-contract, persistence, scope, helper, and UI tests; commit and
  review.

### Task 9: Full-text and study artifacts

**Repository:** citation-renamer.

- Persist versioned page/section text with extractor/source hashes and evidence
  spans instead of title-dependent excerpts.
- Add local-first corpus topic/method/finding/gap synthesis with source links.
- Require hosted-provider consent and bounded evidence preview per run.
- Discover tables and figure metadata; require table review before creating a
  dataset version and defer figure digitization.
- Add standalone supported-format imports through the same dataset path.
- Test provenance, staleness, consent, extraction review, and malformed inputs;
  commit and review.

### Task 10: Gallery-derived visualization studio

**Repositories:** Gallery inventory as read-only source; implementation in
citation-renamer.

- Audit licenses/assets and extract reviewed bar, line, scatter, heatmap,
  treemap, and network templates into a versioned manifest/renderer bundle.
- Add native mapping/filter/aggregation/recommendation controls and persist
  visualization specs against dataset versions.
- Run renderer offline in an ephemeral locked-down WKWebView with CSP and safe
  data injection.
- Provide native accessible summary/table fallback and SVG/PNG/PDF/reviewed HTML
  export.
- Test zero network access, injection resistance, deterministic render,
  compatibility guidance, accessibility, and exports; commit and review.

### Task 11: Deployment and end-to-end verification

- Run complete Saturn and Citewrite test/build suites.
- Deploy the reviewed Saturn release to drummer with backup/health rollback.
- Smoke profile/compare/helper jobs and `dr.eamer.dev/saturn/`.
- Verify the Citewrite collection-analysis vertical slice against golden and
  real local collections.
- Perform final whole-branch reviews, resolve important findings, and prepare
  deliberate merge/publish handoffs for both repositories.
