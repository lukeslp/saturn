# Saturn Workbench Plan

Status: recommended direction; two validation spikes remain

Last verified: 2026-08-02

Canonical home: this document in the `lukeslp/saturn` repository

Pickup card:

- Baselines: Saturn `2e2e69a`; Citewrite `8c91f67`.
- Next action: prototype the mixed-file Drop Shelf.
- Second action: package a minimal signed universal Saturn worker.
- Hold: no rebrand, engine merger, or silent file automation before both
  spikes pass.
- Citewrite integration brief:
  `docs/specs/saturn-workbench-integration.md` in
  `lukeslp/citation-renamer`.

## Decision

Build one shared architecture and one universal capture experience. Keep the
specialized workspaces modular.

The long-term desktop product is **Saturn Workbench**, a native macOS app that
accepts untidy groups of local files and remote data references, understands
what they are, and proposes useful next actions. A menu-bar Drop Shelf is the
common front door. Papers, datasets, and media then move into task-specific
workspaces with their own review, analysis, and output rules.

Citewrite remains available as a focused Papers distribution built from the
same macOS modules. The current Python Saturn CLI and server remain the
authoritative full dataset engine. This preserves a small, focused Citewrite
while allowing a broader Saturn app to compose Papers, Data, and Media.

The repository strategy is:

- `lukeslp/saturn` owns this plan, the language-neutral findings contract, the
  Python engine, dataset connectors, and the web/MCP surfaces.
- `lukeslp/citation-renamer` owns the current Citewrite app and the first macOS
  implementations of secure capture, review-gated file work, persistence,
  Keychain access, security-scoped bookmarks, native analysis, and rendering.
- The macOS code may begin in the Citewrite workspace as extracted Swift
  packages and a second app target. Repository consolidation is a later
  decision. Shared boundaries should settle before source trees move.

## Product thesis

Saturn turns an unruly drop of files or URLs into a reviewed, traceable
workspace.

The Drop Shelf is the unifying interaction:

```text
Menu-bar Drop Shelf / Open / Paste URL / Search
                         |
                         v
                capture and fingerprint
             classify, group, record source
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
       Papers           Data           Media
      Citewrite         Saturn       images first
          +--------------+--------------+
                         |
                         v
              proposed action plan
          Review -> Apply -> History -> Undo
```

The drawer is temporary staging, not a permanent pile. A drop creates an
immutable `CaptureBatch`. Read-only inventory and bounded local analysis can
start immediately. File mutations, remote submissions, license acceptance,
and paid model calls remain explicit.

A mixed batch might become:

- eight papers routed to citation extraction and verification;
- two CSV files proposed as comparable dataset versions;
- fourteen images grouped by exact or perceptual similarity, with descriptive
  filename and alt-text proposals;
- three unknown files left untouched for manual routing.

## Product surfaces

### Saturn Workbench for macOS

The umbrella app contains:

- **Inbox**: captured groups, routing decisions, pending action plans, history,
  and undo;
- **Papers**: Citewrite's document inventory, extraction, catalog
  reconciliation, citation review, rename, consolidation, and export;
- **Data**: local and remote dataset discovery, materialization, profiling,
  comparison, visualization, and reproducible export;
- **Media**: image metadata, OCR/vision evidence, duplicate grouping, accepted
  descriptions, descriptive filenames, and non-destructive output. Audio and
  video require later format-specific specifications.

The app should use progressive disclosure. A person handling a CSV should not
see citation controls. A person cleaning images should not choose Hugging Face
splits. The Inbox shows routing and status; each domain workspace owns its full
editor.

### Citewrite for macOS

Citewrite remains a focused Papers app and App Store candidate. It consumes
the shared capture, action-plan, provenance, contract, and rendering packages
without bundling every connector or the full Python scientific runtime.

Citewrite can import a reviewed Saturn bundle into a research project. Its
lightweight native analyzer remains useful for bounded offline previews and
small accepted datasets. The interface must identify which engine produced a
finding and must not imply numerical equivalence until conformance tests prove
it.

### Saturn CLI

The CLI remains the expert and automation surface. It owns full-corpus
profiling, broad file-format support, comparisons, deterministic findings,
self-contained reports, and optional model interpretation.

Desktop work should strengthen these programmatic boundaries rather than
burying them in UI code.

### Saturn Web

The initial web product is discovery-oriented: search, metadata, previews,
public findings, and direct downloads. Remote analysis becomes public only
after authentication, per-user storage, quotas, retention, and private-result
controls exist.

The current managed viewer is healthy on loopback port 5043. The public
`/saturn/` route still serves a static archive, and Caddy does not proxy the
mutation service. Preserve that boundary until the web hardening gates in this
plan pass.

### Saturn MCP

MCP is an adapter over the application layer, not the implementation of the
desktop app. A later server can expose:

- `dataset.search`
- `dataset.describe`
- `dataset.download`
- `analysis.run`
- `analysis.compare`
- `analysis.get`

The official Hugging Face MCP server is useful for agent exploration. Saturn's
deterministic product path should use provider APIs and SDKs directly.

## Architecture

### macOS package boundaries

Proposed modules and responsibilities:

```text
WorkbenchShell
  CaptureKit
    Drop Shelf, Open/Paste intake, CaptureBatch, routing proposals
  ArtifactKit
    identities, revisions, hashes, bookmarks, immutable materialization
  ActionPlanKit
    proposed operations, preconditions, collisions, journals, undo
  CredentialKit
    Keychain references and provider account state; never secret persistence
  SaturnContractKit
    versioned findings models, validators, fixtures, conformance corpus
  ConnectorKit
    catalog search, manifests, selection, materialization, provenance
  ReportKit
    contract readers, secure offline rendering, exports

Domain modules
  PapersWorkbench
  DatasetWorkbench
  MediaWorkbench

Process boundaries
  NativeAnalysis.xpc
  FullSaturnWorker.xpc or a separately packaged signed worker

Products
  Citewrite.app       Papers-focused composition
  Saturn.app          Inbox + Data first; Papers and Media added by gates
```

No domain module calls another domain module directly. They exchange neutral
artifact manifests, findings contracts, and reviewed bundles through the
shell. This keeps shared infrastructure reusable without producing a generic
`analyzeAnything()` service.

### Capture model

`CaptureBatch` records one user gesture and preserves grouping:

- stable batch identifier and capture time;
- source application or intake surface when available;
- file bookmarks or remote references;
- original names, sizes, types, and SHA-256 hashes;
- read-only classification results with model/rule provenance;
- proposed workspace and destination;
- explicit user intent when the person chose an action at capture time;
- processing state and cancellation state.

Captured local files stay in place until a reviewed action plan says
otherwise. Membership in a Saturn workspace does not require moving the
source. Remote references do not download until size, license, gate, revision,
and destination are known.

### Artifact and revision model

An `Artifact` is a stable logical item. An `ArtifactRevision` is immutable and
content-addressed. Each revision records:

- provider and resource kind;
- stable provider identifier;
- immutable revision, commit, or version;
- selected config, split, files, shards, table, or sheet;
- original and resolved URLs;
- byte sizes and content hashes;
- media type and detected tabular format;
- license and gate state, including the user's acknowledgement;
- retrieval time;
- exact, streamed, sampled, or partial semantics;
- parent capture batch and derived-output lineage.

Large row payloads belong in content-addressed files. SwiftData stores
metadata, relationships, small reviewed evidence, and result references. It
should not duplicate entire remote corpora as row-oriented JSON blobs.

### Action plans and trust

Every write-capable domain produces an `ActionPlan`. Plans contain exact source
preconditions, proposed destinations, collision outcomes, expected outputs,
and consent requirements. Applying a plan writes an `ActionJournal` before it
reports success.

Automation graduates through four trust levels:

1. **Observe**: inventory and propose only.
2. **Assist**: run bounded local read-only analysis automatically.
3. **Remember**: suggest a prior decision for the same scoped source and type.
4. **Automate**: apply a user-promoted, narrowly scoped rule, still stopping on
   changed hashes, conflicts, new categories, license gates, or network use.

A learned rule includes its source folder or provider, artifact type, intended
workspace, destination, and write policy. A single `.csv` decision must not
become a global rule for every CSV on the Mac.

Network submission, destructive deletion, replacement of an existing file,
new provider terms, and paid model use always require fresh or explicitly
persisted consent appropriate to that operation.

## Dataset connector boundary

Connectors discover and materialize data. Analyzers consume local immutable
artifacts. Keep these responsibilities separate.

```text
search(query, filters, cursor) -> DatasetSummaryPage
describe(reference)           -> DatasetManifest
materialize(selection, destination, progress)
                              -> MaterializedDataset
```

`DatasetSummary` includes provider identity, title, owner, description,
license/gate summary, update time, popularity signals, and a stable reference.

`DatasetManifest` includes versions, files, configs, splits, tables, sizes,
formats, license details, and preview capabilities.

`MaterializedDataset` contains only pinned local files plus complete
provenance. The analyzer receives local paths and a manifest. It receives no
provider credential and has no network entitlement.

Use explicit source URIs at every machine boundary:

- `hf://owner/dataset@revision`
- `kaggle://owner/dataset@version`
- `github://owner/repository@commit/path`
- `file://` only inside already authorized local handoffs

Do not retain Saturn's current heuristic that treats any nonexistent string
containing `/` as Hugging Face once multiple providers exist.

### Hugging Face

Hugging Face is the first remote connector.

1. Search the Hub dataset catalog.
2. Inspect the dataset card, license, gate state, configs, splits, sizes, and
   available Parquet conversions.
3. Preview through the Dataset Viewer API.
4. Let the user select configs, splits, and files.
5. Pin an immutable revision and materialize data-only artifacts.
6. Never execute downloaded repository or dataset code.
7. Store the user's token in Keychain and use the user's identity for private
   or gated access. A server credential never accepts terms for a user.

The first implementation should prefer Dataset Viewer metadata and Parquet
URLs. The existing `datasets.load_dataset()` path remains a CLI fallback until
its execution and provenance behavior meets the desktop threat model.

### GitHub

GitHub initially means discovering supported data files or release assets at a
pinned commit.

1. Search repository metadata, topics, README text, and supported filenames.
2. Inspect the tree or release assets.
3. Detect CSV, JSON/JSONL, Parquet, Arrow/Feather, spreadsheets, SQLite, and
   other explicitly supported data artifacts.
4. Require file selection and show the exact pinned commit.
5. Handle Git LFS pointers and submodules explicitly.

Do not clone and analyze arbitrary repositories, infer that one repository is
one table, or execute repository code.

### Kaggle

Kaggle follows its versioned dataset model.

1. Authenticate through the supported browser OAuth flow or a user token.
2. Search datasets and show owner, version, size, license, and usability
   metadata.
3. Require any competition or dataset agreement on Kaggle under the user's
   identity.
4. Download a selected version with resume support.
5. Treat the archive as untrusted: cap expansion, reject absolute paths and
   traversal, and let the user select contained artifacts.

### Local files

Local intake uses security-scoped bookmarks and the existing review-first
preview pattern. The full Saturn engine currently supports CSV, TSV,
JSON/JSONL, Parquet, Arrow/Feather, spreadsheets, SQLite, and GeoJSON. The
native engine supports bounded CSV and row-oriented JSON today; Arrow/Feather
remains planned.

## Findings contract

### Current state

Contract v1 is a real cross-language seam:

- Saturn and Citewrite carry byte-identical canonical profile and comparison
  fixtures.
- Both validate the same strict top-level shape.
- Saturn's helper emits version-1 findings; Citewrite's native XPC analyzer
  also emits version-1 findings.

Shape compatibility does not guarantee semantic compatibility. Known drift
includes:

- Citewrite comparison currently emits `delta["mean"]`; Saturn and its viewer
  use `mean_delta`, `null_rate_delta`, and related names.
- The engines use different string-column classification thresholds.
- The engines use different correlation algorithms and performance bounds.
- Citewrite tests cover small schema-compatible examples, not numerical parity
  across representative corpora.

### Contract work

Phase 0 preserves v1 and creates one neutral contract package containing:

- the JSON Schema;
- canonical valid fixtures;
- a shared malformed-document corpus;
- typed metric and delta definitions;
- feature/capability declarations;
- cross-language golden analyses for representative datasets;
- versioning and migration rules.

Use `extensions.engineCapabilities` in v1 to distinguish current engines while
contract v2 takes shape. Contract v2 should promote these fields:

- complete source and artifact provenance;
- engine identity and capability set;
- per-metric exact/sampled/partial semantics;
- typed profile statistics and comparison deltas;
- warnings for truncation, fallback, or unsupported metrics;
- stable extension namespaces.

One engine becomes authoritative for each declared capability. The UI shows
the engine name and semantics. It does not blend incomparable metrics.

## Analysis process boundaries

### Native analysis

Citewrite's embedded XPC service is the safe lightweight path:

- signed peer and containing-app checks;
- no network entitlement;
- bounded requests and results;
- duplicate-request rejection;
- progress, cancellation, and timeouts;
- deterministic profile and comparison output.

Extract and rename this boundary only after its tests protect the existing
Citewrite release. It remains appropriate for previews, small accepted
datasets, and App Store builds.

### Full Saturn analysis

The Python helper already provides a good machine protocol: a strict job
envelope, one confined job directory, Arrow or JSON inputs, newline-delimited
progress, cancellation, atomic output, and contract validation.

Its current dependency environment is too large to assume desktop packaging
will work. The deployed Linux environment is about 1.1 GB, while the current
Citewrite DMG is about 8 MB. Citewrite previously replaced an external Saturn
helper authorization flow with native XPC analysis. Restoring a user-installed
Python environment is not an acceptable product design.

The first engineering spike packages a minimal, self-contained, signed and
notarized universal macOS worker. It must prove:

- no system Python dependency;
- no downloaded executable code;
- both Apple silicon and Intel execution;
- a sanitized environment and no network access;
- per-job path confinement;
- cancellation and process cleanup;
- bounded memory and disk use on realistic wide and multi-gigabyte inputs;
- acceptable cold-start time and installed size;
- an update and dependency-vulnerability strategy.

Direct Developer ID distribution is the first target. App Store packaging
remains a separate acceptance gate. If the full worker is too large or cannot
meet the sandbox, the product keeps native previews and offers the full engine
as a separately installed, explicitly paired Saturn component or remote job
under a separate privacy contract.

## Media boundary

The existing Citewrite image-workspace specification remains the starting
point for Media:

- source hashing and exact/perceptual duplicate groups;
- local-only bounded vision input;
- separate proposed and accepted alt text and filename;
- explicit decorative state;
- non-destructive copies by default;
- sidecar and optional PNG/JPEG metadata embedding;
- hash-checked journals and undo.

Media shares capture, artifact identity, naming safety, and action journals.
It owns image decoding, EXIF/IPTC/XMP, perceptual similarity, visual review,
and output transforms. Audio and video later need AVFoundation metadata,
codec, duration, thumbnail, transcript, and sidecar decisions. They should not
inherit image rules implicitly.

## Security and privacy invariants

- Read-only inventory precedes every proposed mutation.
- Changed source hashes invalidate pending plans.
- Review remains the default write gate.
- Rename is collision-safe, journaled, scoped to an authorized root, and
  undoable.
- Consolidation and media output copy by default and verify outputs.
- Credentials stay in Keychain or process memory. Databases store references,
  never secret values.
- The networked downloader and no-network analyzer remain separate.
- Remote analysis shows the destination and evidence projection before
  dispatch.
- Raw rows, documents, and media never leave the Mac through an implicit
  workflow.
- Downloads are data, never executable extensions or plug-ins.
- Archives have file-count, byte, expansion-ratio, and traversal limits.
- Caches have explicit quotas, retention, and eviction.
- Every remote artifact records license and gate state.

### Web hardening gates

Do not expose the current mutation routes through Caddy until all of these are
implemented and tested:

- authenticated identity;
- per-user findings and download namespaces;
- CSRF protection for browser mutations;
- request and job rate limits;
- per-user compute, disk, and provider-spend quotas;
- durable jobs or an explicitly documented single-process failure model;
- private results by default;
- upload, HF cache, and result retention controls;
- server-key isolation from anonymous jobs;
- consent and redaction for model evidence;
- representative abuse, traversal, and data-disclosure tests.

## Delivery plan

### Phase 0: make the seams truthful

Goal: shared contracts and explicit engine capabilities.

- Move schema, fixtures, malformed cases, and golden analyses into a neutral
  versioned contract package.
- Add cross-language conformance checks to Saturn and Citewrite CI.
- Document current semantic differences and align or namespace metric keys.
- Add `engineCapabilities` and exact/sampled/partial declarations.
- Replace ambiguous source-string dispatch with explicit provider URIs.
- Specify `CaptureBatch`, `ArtifactRevision`, `ActionPlan`, `ActionJournal`,
  and reviewed Saturn bundle schemas.

Exit criteria:

- both repositories consume the same pinned contract release;
- representative golden datasets either match numerically within documented
  tolerances or advertise different capabilities;
- no existing Citewrite or Saturn output loses compatibility.

### Phase 1: prove the two highest-risk ideas

Run two independent spikes.

**Product spike: Drop Shelf**

- Add a menu-bar drop target and ordinary Open/Paste intake.
- Preserve a mixed group as one `CaptureBatch`.
- Route PDF, CSV/JSON, and image fixtures to Papers, Data, and Media proposals.
- Perform read-only inventory and bounded analysis.
- Show one review screen with per-item and group acceptance.
- Apply a safe copy/rename plan and undo it through the journal.
- Start with a permanent menu-bar target that needs no Accessibility
  permission. Treat an automatically appearing target as an optional UX
  experiment.

**Engineering spike: full worker**

- Produce the smallest practical universal worker with web and model extras
  removed.
- Sign, notarize, install, launch, cancel, and update it.
- Benchmark cold start, package size, RAM, disk, and analysis time.
- Exercise wide, nested, malformed, multi-gigabyte, and cancellation fixtures.

Exit criteria:

- a mixed drop reaches reviewed, reversible outcomes without domain leakage;
- the full worker meets explicit size and performance budgets, or the team
  chooses the optional-component fallback before building UI around it.

### Phase 2: Saturn Desktop 0.1

Scope: Inbox + local Data + Hugging Face.

- Build the native shell using extracted Citewrite patterns.
- Add local file/folder authorization and immutable materialization.
- Implement HF search, manifest, preview, selection, pinned download, cache,
  and provenance.
- Integrate the authoritative worker selected in Phase 1.
- Persist dataset versions, analysis runs, comparisons, and reports.
- Export a reviewed Saturn bundle that Citewrite can import.
- Ship first through a signed and notarized Developer ID DMG.

Deliberately excluded:

- Kaggle;
- GitHub repository discovery;
- automatic watched folders;
- public remote analysis;
- audio/video;
- a marketplace or downloaded plug-ins.

### Phase 3: focused-product interoperability

- Add `Open in Saturn` and Saturn-bundle import to Citewrite.
- Extract shared capture, action-plan, contract, credential, and report
  packages without changing Citewrite's review gates.
- Keep Citewrite as a focused app target.
- Decide whether Saturn's umbrella build includes Papers based on actual mixed
  capture use, bundle impact, and navigation tests.
- Implement the reviewed image workspace behind the shared Inbox.

### Phase 4: GitHub and Kaggle

- Add GitHub supported-file and release-asset discovery at pinned commits.
- Add Kaggle OAuth, search, version/license inspection, safe archive download,
  and file selection.
- Add resumable downloads, provider-specific quotas, and cache controls.
- Validate cross-provider comparisons through materialized local artifacts.

### Phase 5: automation and broader media

- Add narrowly scoped learned routing rules and watched folders.
- Add scheduling only after journals, conflicts, and battery impact are
  observable.
- Add audio/video through separate reviewed specifications.
- Consider Finder Quick Actions and Share extensions as additional intake
  surfaces.

### Phase 6: hardened web and MCP

- Add identity, tenancy, quotas, private results, retention, and durable job
  state to the web service.
- Expose public mutations only after the hardening checklist passes.
- Add Saturn MCP as a thin adapter over the same connector and analysis job
  application services.

## Go and stop conditions

Proceed when:

- a pinned source revision and complete provenance survive search, download,
  analysis, export, and Citewrite import;
- downloaded artifacts cannot execute code;
- realistic jobs stream or remain within explicit RAM and disk budgets;
- engine metrics are conformant or visibly capability-scoped;
- tokens use Keychain and gated access uses the user's identity;
- every file mutation can be previewed and hash-safely undone;
- the universal worker signs, notarizes, and launches on both architectures at
  an acceptable size and cold-start time.

Stop and redesign if work requires:

- restoring a user-authorized external Python virtual environment;
- shipping Hugging Face, GitHub, Kaggle, Papers, and Media in one first
  release;
- calling two semantic implementations the same engine because their JSON
  shapes match;
- anonymous server-side analysis with shared credentials;
- cloning or executing arbitrary GitHub repositories;
- using the menu-bar panel as the full workbench;
- weakening Citewrite's review, file-safety, or privacy invariants.

## Immediate backlog

The next person picking this up should work in this order:

1. Open an architecture decision record for the shared contract package and
   choose its versioning and consumption strategy.
2. Add a cross-engine golden corpus that exposes the current `mean` versus
   `mean_delta` mismatch and classification differences.
3. Specify the five neutral macOS models: `CaptureBatch`, `ArtifactRevision`,
   `ActionPlan`, `ActionJournal`, and `SaturnBundle`.
4. Prototype the smallest menu-bar Drop Shelf with a mixed local fixture set.
5. Audit Saturn's dependency import graph and produce a minimal worker-size
   budget before packaging.
6. Implement HF catalog search and manifest retrieval separately from the
   current `HFAdapter` materialization path.
7. Define web authentication, tenancy, quotas, and retention before changing
   Caddy.

## Verification baseline

The architectural review that produced this plan verified:

- Saturn source and active release at commit `2e2e69a`;
- Citewrite source at commit `8c91f67`;
- clean test suites for both projects;
- a healthy Saturn service bound to `127.0.0.1:5043`;
- a static public `/saturn/` archive with no live Caddy proxy;
- a signed, notarized, universal Citewrite 1.0.1 build 23 DMG;
- byte-identical contract-v1 profile and comparison fixtures in both repos;
- no separate Saturn macOS app or DMG;
- no implemented Saturn catalog search, GitHub dataset connector, or Kaggle
  connector.

Re-verify live state before relying on these facts in a later session.

## References

Project references:

- `saturn/ingestion.py`
- `saturn/helper.py`
- `saturn/core/contract.py`
- `saturn/core/schemas/contract-v1.schema.json`
- `docs/DEPLOY.md`
- Citewrite `docs/local-analysis.md`
- Citewrite `docs/specs/image-analysis-workspace.md`
- Citewrite `docs/specs/arrow-feather-import.md`
- Citewrite `docs/specs/saturn-workbench-integration.md`

External references:

- [Hugging Face Dataset Viewer API](https://huggingface.co/docs/dataset-viewer/en/quick_start)
- [Hugging Face Hub API](https://huggingface.co/docs/hub/en/api)
- [Hugging Face MCP server](https://huggingface.co/docs/hub/en/agents-mcp)
- [GitHub repository search API](https://docs.github.com/en/rest/search/search)
- [GitHub contents and archive API](https://docs.github.com/en/rest/repos/contents)
- [Kaggle CLI documentation](https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md)
- [Spotless Drop Target and task model](https://lightpillar.com/spotless-detailed.html)
- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Apple App Sandbox documentation](https://developer.apple.com/documentation/security/protecting-user-data-with-app-sandbox)
