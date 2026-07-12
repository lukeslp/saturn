# Citewrite Research & Visualization Workbench Design

## Product direction

Citewrite will become a project-based desktop research workbench. A project can
contain citation collections, document content, reviewed tables extracted from
studies, imported datasets, analysis runs, and visualization specifications.
Saturn supplies deterministic profiling, comparison, and visualization
recommendations through a versioned cross-language contract. Gallery supplies a
curated pattern corpus; Citewrite does not execute its arbitrary pages.

## System boundaries

- Citewrite owns projects, security-scoped files, document and dataset
  persistence, consent, analysis history, native navigation, and exports.
- Saturn owns tabular profiling, compatible comparisons, evidence projection,
  and visualization-template recommendations.
- The first integration invokes a user-configured local Saturn helper with JSON
  job envelopes and Arrow IPC inputs. Python bundling is deferred until the
  contract is proven.
- Visualization uses native SwiftUI controls around a bundled, offline,
  locked-down WKWebView renderer containing reviewed templates only.

## Delivery sequence

1. Repair Saturn correctness, serialization, ingestion, job retention, and
   packaging; deploy the repaired test instance to drummer.
2. Extract a dependency-light `saturn.core` with findings contract version 1
   and a machine helper interface.
3. Add Citewrite research projects and collection-metadata analysis.
4. Add page-provenanced full-text extraction and local-first corpus synthesis.
5. Add reviewed table extraction and standalone dataset import.
6. Add Gallery-derived visualization recommendations, editing, and export.

## Invariants

- Deterministic analysis never requires a model provider.
- Results contain only finite JSON numbers and preserve reproducibility inputs.
- Comparisons never coerce one side into the other side's semantic type.
- Redaction removes literal dataset values from every evidence surface.
- Citewrite analyzes accepted citations by default; draft items require explicit
  inclusion and a visible warning.
- Hosted document synthesis is per-run opt-in with a bounded evidence preview.
- Extracted tables require review before becoming dataset versions.
- Figures are catalogued first; automatic chart digitization is deferred.
- Renderers have no network, telemetry, arbitrary script, or remote-template
  access and always provide an accessible native summary/table fallback.

## Acceptance

Each phase is independently shippable and tested. The first user-visible
milestone is a native Citewrite collection-analysis workspace whose aggregates
link back to source citations. Later imported and extracted data use the same
Saturn dataset/profile contracts and the same visualization studio.
