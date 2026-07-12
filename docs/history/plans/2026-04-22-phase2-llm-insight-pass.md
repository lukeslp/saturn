# Phase 2 — Multi-LLM Insight Pass Implementation Plan

> Archived implementation plan. Steps use checkbox (`- [ ]`) syntax to preserve the original progress record.

**Goal:** Add an opt-in `--llm` flag to `saturn analyze` and `saturn compare` that turns the deterministic findings into narrated, cross-critiqued insights via two or more language models, without ever breaking the free-and-deterministic default output.

**Architecture:** A new `saturn/llm.py` module wraps `~/shared/llm_providers.ProviderFactory`. It takes the already-assembled `ReportData` (or `CompareReport`) and emits `list[Insight]` — a dataclass with per-column / per-dataset narratives plus a second-model critique (the "catfish"). Insights are appended to the JSON findings and rendered in a new HTML section. The deterministic stats pass remains untouched; LLMs only ever see aggregated per-column stats, never raw user rows.

**Tech Stack:**
- `~/shared/llm_providers.ProviderFactory` (sole LLM gateway — no direct `anthropic`/`openai` imports anywhere)
- `~/shared/config.ConfigManager` for API keys (fallback: env vars from `.env.example`)
- Dataclasses (matches saturn's existing shape)
- Jinja2 (existing)
- pytest with recorded fixtures (no live network in CI)

---

## Pre-flight

- [ ] **Step P1: Create a worktree (if not already in one)**

```bash
cd /home/coolhand/projects/saturn/saturn
git status   # must be clean
git checkout -b phase2-llm-insight
```

- [ ] **Step P2: Confirm PYTHONPATH includes `~/shared`**

```bash
source venv/bin/activate
export PYTHONPATH=/home/coolhand/shared:$PYTHONPATH
python -c "from llm_providers import ProviderFactory, Message; print(ProviderFactory.list_providers())"
```
Expected: a list containing `anthropic`, `openai`, `groq`, etc.

- [ ] **Step P3: Smoke-test that the existing suite still passes**

```bash
SATURN_NO_NETWORK=1 pytest -q
```
Expected: all tests pass.

---

## Task 1: Insight dataclasses

**Files:**
- Create: `saturn/insights.py`
- Test: `tests/test_insights.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_insights.py
from saturn.insights import Insight, InsightBundle, Critique


def test_insight_serialises_to_dict():
    ins = Insight(
        scope="column",
        target="alt_text",
        narrative="The column is 98% English with 8.5% duplicates.",
        confidence="high",
        evidence_keys=["null_rate", "language_counts", "duplicate_rate"],
        model="anthropic:claude-sonnet-4-6",
    )
    d = ins.to_dict()
    assert d["scope"] == "column"
    assert d["target"] == "alt_text"
    assert d["evidence_keys"] == ["null_rate", "language_counts", "duplicate_rate"]


def test_critique_attaches_to_insight():
    ins = Insight(scope="dataset", target="__global__", narrative="looks fine", confidence="medium",
                  evidence_keys=[], model="anthropic:claude-sonnet-4-6")
    crit = Critique(
        reviewer_model="openai:gpt-4o",
        verdict="disagree",
        reason="null rate on author_handle is 100% on firehose, that is not 'fine'.",
    )
    ins.critiques.append(crit)
    d = ins.to_dict()
    assert d["critiques"][0]["verdict"] == "disagree"


def test_insight_bundle_roundtrips_json():
    import json
    bundle = InsightBundle(
        providers=["anthropic:claude-sonnet-4-6", "openai:gpt-4o"],
        insights=[
            Insight(scope="dataset", target="__global__", narrative="x", confidence="high",
                    evidence_keys=["row_count"], model="anthropic:claude-sonnet-4-6"),
        ],
        total_usage={"input_tokens": 123, "output_tokens": 45},
    )
    serialised = json.dumps(bundle.to_dict())
    restored = json.loads(serialised)
    assert restored["providers"] == ["anthropic:claude-sonnet-4-6", "openai:gpt-4o"]
    assert restored["total_usage"]["input_tokens"] == 123
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `pytest tests/test_insights.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'saturn.insights'`

- [ ] **Step 1.3: Write minimal implementation**

```python
# saturn/insights.py
"""Dataclasses for LLM-generated insights and their cross-model critiques."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Critique:
    reviewer_model: str          # e.g. "openai:gpt-4o"
    verdict: str                 # "agree" | "disagree" | "partial"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Insight:
    scope: str                   # "column" | "dataset" | "compare"
    target: str                  # column name, or "__global__", or "<colA>|<colB>"
    narrative: str               # model-produced prose
    confidence: str              # "high" | "medium" | "low"
    evidence_keys: list[str]     # which findings keys the narrative drew from
    model: str                   # "provider:model_id"
    critiques: list[Critique] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "target": self.target,
            "narrative": self.narrative,
            "confidence": self.confidence,
            "evidence_keys": self.evidence_keys,
            "model": self.model,
            "critiques": [c.to_dict() for c in self.critiques],
        }


@dataclass
class InsightBundle:
    providers: list[str]                              # in order used
    insights: list[Insight] = field(default_factory=list)
    total_usage: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": self.providers,
            "insights": [i.to_dict() for i in self.insights],
            "total_usage": self.total_usage,
            "errors": self.errors,
        }
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `pytest tests/test_insights.py -v`
Expected: 3 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add saturn/insights.py tests/test_insights.py
git commit -m "feat: add Insight/Critique/InsightBundle dataclasses for phase 2"
```

---

## Task 2: Evidence projection (findings → model-ready context)

**Files:**
- Create: `saturn/llm/__init__.py`
- Create: `saturn/llm/evidence.py`
- Test: `tests/test_llm_evidence.py`

**Why its own module:** converting `ReportData` to a compact dict the model can reason about is pure, deterministic, and must stay testable without a network. Keeping it separate from the provider-call code means we can unit-test the prompt inputs without mocking HTTP.

- [ ] **Step 2.1: Write the failing test**

```python
# tests/test_llm_evidence.py
from saturn.profilers import Alert, ProfileResult
from saturn.report import ReportData, DatasetMeta, assemble
from saturn.llm.evidence import column_evidence, dataset_evidence


def _mk_report() -> ReportData:
    results = [
        ProfileResult(
            column="alt_text", kind="text", n=1000, n_null=50, n_unique=950,
            stats={"len_mean": 201.3, "len_p95": 460, "duplicate_rate": 0.085},
            extras={"language_counts": {"en": 900, "es": 50, "__engine": "fasttext:1,000"}},
            alerts=[Alert("info", "multilingual", "2+ languages")],
        ),
        ProfileResult(
            column="cursor", kind="text", n=1000, n_null=0, n_unique=800,
            stats={"duplicate_rate": 0.2},
            extras={},
            alerts=[Alert("warn", "near_unique", "most values distinct")],
        ),
    ]
    return assemble(source="hf://test/demo", row_count=1000, sampled_rows=1000, seed=42,
                    schema={"alt_text": "text", "cursor": "text"}, results=results, mode="full")


def test_column_evidence_extracts_pruned_subset():
    report = _mk_report()
    ev = column_evidence(report, "alt_text")
    assert ev["column"] == "alt_text"
    assert ev["kind"] == "text"
    assert ev["null_rate"] == 0.05
    assert ev["stats"]["len_mean"] == 201.3
    # alerts flattened to code strings only (no message prose, keeps tokens down)
    assert ev["alerts"] == ["multilingual"]
    assert ev["language_counts"] == {"en": 900, "es": 50}
    # provenance key __engine is stripped — it is a saturn implementation detail
    assert "__engine" not in ev["language_counts"]


def test_dataset_evidence_summarises_whole_report():
    report = _mk_report()
    ev = dataset_evidence(report)
    assert ev["source"] == "hf://test/demo"
    assert ev["row_count"] == 1000
    assert ev["column_count"] == 2
    # columns sorted by a coarse "interesting-ness" — alerts present first
    assert ev["columns"][0]["column"] in {"alt_text", "cursor"}
    assert len(ev["columns"]) == 2
    # top-level evidence keys stable for test snapshotting
    assert set(ev.keys()) == {"source", "row_count", "column_count", "kinds", "columns"}
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `pytest tests/test_llm_evidence.py -v`
Expected: FAIL — module not found.

- [ ] **Step 2.3: Write minimal implementation**

```python
# saturn/llm/__init__.py
"""LLM insight pass — opt-in multi-provider narration of deterministic findings."""
```

```python
# saturn/llm/evidence.py
"""Project `ReportData` into a compact, model-ready dict.

Insights pass NEVER sees raw user rows. Only per-column aggregates that
already live in the findings JSON are forwarded — same surface the public
JSON sidecar already exposes.
"""

from __future__ import annotations

from typing import Any

from ..profilers import ProfileResult
from ..report import ReportData


def _prune_language_counts(counts: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in counts.items() if not k.startswith("__")}


def column_evidence(report: ReportData, column: str) -> dict[str, Any]:
    result = next((r for r in report.results if r.column == column), None)
    if result is None:
        raise KeyError(f"column {column!r} not in report")
    ev: dict[str, Any] = {
        "column": result.column,
        "kind": result.kind,
        "n": result.n,
        "null_rate": round(result.null_rate, 4),
        "n_unique": result.n_unique,
        "stats": dict(result.stats),
        "alerts": [a.code for a in result.alerts],
    }
    langs = result.extras.get("language_counts")
    if isinstance(langs, dict):
        ev["language_counts"] = _prune_language_counts(langs)
    top_values = result.extras.get("top_values")
    if top_values:
        ev["top_values"] = top_values[:10]
    top_words = result.extras.get("top_words")
    if top_words:
        ev["top_words"] = top_words[:10]
    return ev


def dataset_evidence(report: ReportData) -> dict[str, Any]:
    by_interest = sorted(
        report.results,
        key=lambda r: (-len(r.alerts), -(r.null_rate or 0.0), r.column),
    )
    return {
        "source": report.meta.source,
        "row_count": report.meta.row_count,
        "column_count": len(report.results),
        "kinds": {r.column: r.kind for r in report.results},
        "columns": [column_evidence(report, r.column) for r in by_interest],
    }
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `pytest tests/test_llm_evidence.py -v`
Expected: 2 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add saturn/llm/ tests/test_llm_evidence.py
git commit -m "feat: evidence projection layer for llm insight pass"
```

---

## Task 3: Prompt builders (deterministic, snapshot-tested)

**Files:**
- Create: `saturn/llm/prompts.py`
- Test: `tests/test_llm_prompts.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/test_llm_prompts.py
from saturn.llm.prompts import (
    build_column_prompt,
    build_dataset_prompt,
    build_critique_prompt,
    PROMPT_VERSION,
)


def test_column_prompt_is_deterministic_and_cites_evidence_keys():
    ev = {
        "column": "alt_text",
        "kind": "text",
        "n": 1000,
        "null_rate": 0.05,
        "n_unique": 950,
        "stats": {"len_mean": 201.3, "duplicate_rate": 0.085},
        "alerts": ["multilingual"],
        "language_counts": {"en": 900, "es": 50},
    }
    sys, user = build_column_prompt(ev)
    # Deterministic: same input always produces same string
    assert build_column_prompt(ev) == (sys, user)
    # Contract: narrative must cite these keys
    assert "evidence_keys" in sys
    assert "len_mean" in user
    assert "alt_text" in user
    # No raw rows leaked (prompt must not contain the string "content:" or similar)
    assert "row" not in user.lower() or "row_count" in user.lower()


def test_dataset_prompt_includes_version_tag():
    ev = {"source": "hf://x/y", "row_count": 10, "column_count": 2,
          "kinds": {"a": "text", "b": "numeric"}, "columns": []}
    sys, user = build_dataset_prompt(ev)
    assert PROMPT_VERSION in sys


def test_critique_prompt_references_target_insight():
    peer = {
        "scope": "column",
        "target": "alt_text",
        "narrative": "This column is mostly English with some duplicates.",
        "confidence": "high",
        "evidence_keys": ["null_rate", "language_counts"],
        "model": "anthropic:claude-sonnet-4-6",
    }
    ev = {"column": "alt_text", "alerts": ["multilingual"], "language_counts": {"en": 900, "es": 50, "fr": 50}}
    sys, user = build_critique_prompt(peer_insight=peer, evidence=ev)
    assert "alt_text" in user
    assert "claude-sonnet-4-6" in user
    # Critic must be told to return a verdict string from the allowed set
    assert "agree" in sys and "disagree" in sys and "partial" in sys
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `pytest tests/test_llm_prompts.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3.3: Write minimal implementation**

```python
# saturn/llm/prompts.py
"""Prompt builders for the insight pass.

Kept deterministic (string concatenation, no randomness) so tests can snapshot
outputs and so saturn runs are reproducible by prompt-version tag.
"""

from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "saturn-insight-v1"

_SYSTEM_COLUMN = f"""You are analysing a single column from a dataset profile emitted by saturn, a deterministic dataset dissector.
Return a single JSON object with keys: narrative (string, 2-4 sentences, plain prose), confidence ("high"|"medium"|"low"), evidence_keys (array of stat keys you used).
Rules:
- Only cite numbers that appear verbatim in the evidence payload.
- Never speculate beyond the evidence. If a signal is missing, say so.
- Flag any signal that would surprise an analyst (severe skew, drift, duplicates, language mix).
- No filler prose. No apologies. No disclaimers.
Tag: {PROMPT_VERSION}
"""

_SYSTEM_DATASET = f"""You are summarising the top-level shape of a dataset from saturn's findings.
Return a single JSON object with keys: narrative (string, 3-6 sentences), confidence ("high"|"medium"|"low"), evidence_keys (array of stat keys you used), hotspots (array of column names worth follow-up).
Rules:
- Do not list every column. Pick the 3-5 that would change a downstream decision.
- Never invent numbers. Only cite values present in the evidence payload.
Tag: {PROMPT_VERSION}
"""

_SYSTEM_CRITIC = f"""You are the critic. Review a peer model's insight about a saturn finding.
Return a single JSON object with keys: verdict ("agree"|"disagree"|"partial"), reason (one sentence citing the specific stat that supports your verdict).
Rules:
- Only cite numbers present in the evidence payload.
- "agree" means the peer's narrative is accurate AND complete given the evidence.
- "partial" means partially accurate but missing something material.
- "disagree" means factually wrong.
- One sentence. No hedging.
Tag: {PROMPT_VERSION}
"""


def _as_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, indent=2)


def build_column_prompt(evidence: dict[str, Any]) -> tuple[str, str]:
    user = f"Column evidence:\n{_as_json(evidence)}"
    return _SYSTEM_COLUMN, user


def build_dataset_prompt(evidence: dict[str, Any]) -> tuple[str, str]:
    user = f"Dataset evidence:\n{_as_json(evidence)}"
    return _SYSTEM_DATASET, user


def build_critique_prompt(peer_insight: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, str]:
    user = (
        f"Peer model: {peer_insight['model']}\n"
        f"Peer insight:\n{_as_json(peer_insight)}\n\n"
        f"Original evidence:\n{_as_json(evidence)}"
    )
    return _SYSTEM_CRITIC, user
```

- [ ] **Step 3.4: Run test to verify it passes**

Run: `pytest tests/test_llm_prompts.py -v`
Expected: 3 PASS.

- [ ] **Step 3.5: Commit**

```bash
git add saturn/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat: deterministic prompt builders for saturn-insight-v1"
```

---

## Task 4: Provider gateway (shared infra wrapper)

**Files:**
- Create: `saturn/llm/gateway.py`
- Test: `tests/test_llm_gateway.py`

**Why a gateway:** the factory is opinionated (env vars, caching, complexity routing). Saturn only needs: `(provider_spec, system, user) → (content, usage)`. Isolating that surface lets the rest of saturn not know about `~/shared`.

- [ ] **Step 4.1: Write the failing test**

```python
# tests/test_llm_gateway.py
from unittest.mock import MagicMock, patch

import pytest

from saturn.llm.gateway import ProviderSpec, call_provider, parse_provider_spec


def test_parse_provider_spec_defaults_model():
    spec = parse_provider_spec("anthropic")
    assert spec.provider == "anthropic"
    assert spec.model is None


def test_parse_provider_spec_parses_colon():
    spec = parse_provider_spec("openai:gpt-4o-mini")
    assert spec.provider == "openai"
    assert spec.model == "gpt-4o-mini"


def test_parse_provider_spec_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        parse_provider_spec("nonsense")


def test_call_provider_wires_messages_and_returns_content_usage():
    fake_response = MagicMock()
    fake_response.content = '{"narrative": "x", "confidence": "high", "evidence_keys": []}'
    fake_response.usage = {"input_tokens": 10, "output_tokens": 5}
    fake_response.model = "claude-sonnet-4-6"

    fake_provider = MagicMock()
    fake_provider.complete.return_value = fake_response

    with patch("saturn.llm.gateway.ProviderFactory") as mock_factory:
        mock_factory.create_provider.return_value = fake_provider
        content, usage = call_provider(
            ProviderSpec(provider="anthropic", model="claude-sonnet-4-6"),
            system="sys",
            user="user",
            api_key="sk-test",
        )

    assert content.startswith("{")
    assert usage == {"input_tokens": 10, "output_tokens": 5}
    mock_factory.create_provider.assert_called_once_with(
        "anthropic", api_key="sk-test", model="claude-sonnet-4-6"
    )
    # Must send a system message + user message, in that order
    messages = fake_provider.complete.call_args[0][0]
    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "sys"
    assert messages[1].content == "user"
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `pytest tests/test_llm_gateway.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4.3: Write minimal implementation**

```python
# saturn/llm/gateway.py
"""Thin wrapper over shared.llm_providers.ProviderFactory.

Everything saturn sends to an LLM goes through here. No direct vendor SDK
imports elsewhere in the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_PROVIDERS = {
    "anthropic", "openai", "groq", "gemini", "mistral", "cohere",
    "xai", "perplexity", "huggingface", "ollama",
}


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    model: str | None = None

    def label(self) -> str:
        return f"{self.provider}:{self.model or 'default'}"


def parse_provider_spec(raw: str) -> ProviderSpec:
    if ":" in raw:
        provider, model = raw.split(":", 1)
    else:
        provider, model = raw, None
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}. allowed: {', '.join(sorted(ALLOWED_PROVIDERS))}"
        )
    return ProviderSpec(provider=provider, model=model)


def call_provider(
    spec: ProviderSpec,
    *,
    system: str,
    user: str,
    api_key: str,
    **provider_kwargs: Any,
) -> tuple[str, dict[str, int]]:
    """Issue one completion. Returns (raw_content, usage_dict)."""
    # Local import so tests that patch ProviderFactory via the string path work.
    from llm_providers import Message  # type: ignore

    from .gateway import ProviderFactory  # re-export below; keeps patchability

    provider = ProviderFactory.create_provider(spec.provider, api_key=api_key, model=spec.model)
    messages = [Message(role="system", content=system), Message(role="user", content=user)]
    response = provider.complete(messages, **provider_kwargs)
    usage = dict(response.usage) if response.usage else {}
    return response.content, usage


# Import at module level too so `with patch("saturn.llm.gateway.ProviderFactory")` works.
from llm_providers import ProviderFactory  # noqa: E402
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `pytest tests/test_llm_gateway.py -v`
Expected: 4 PASS.

- [ ] **Step 4.5: Commit**

```bash
git add saturn/llm/gateway.py tests/test_llm_gateway.py
git commit -m "feat: provider gateway for saturn insight pass (no direct sdk imports)"
```

---

## Task 5: JSON response parsing (tolerant of prose scaffolding)

**Files:**
- Create: `saturn/llm/parsing.py`
- Test: `tests/test_llm_parsing.py`

**Why its own module:** models sometimes wrap JSON in markdown fences or prefix it with "Here is the JSON:". We need one tolerant parser with tests, not ad-hoc regex scattered through the engine.

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_llm_parsing.py
import pytest

from saturn.llm.parsing import extract_json, parse_insight_payload, parse_critique_payload


def test_extract_json_bare():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    raw = 'Sure, here is the JSON:\n```json\n{"a": 1}\n```\nHope that helps.'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_raises_on_no_object():
    with pytest.raises(ValueError, match="no JSON object found"):
        extract_json("there is no json here")


def test_parse_insight_payload_validates_required_keys():
    payload = {"narrative": "x", "confidence": "high", "evidence_keys": ["a"]}
    parsed = parse_insight_payload(payload)
    assert parsed["narrative"] == "x"
    assert parsed["confidence"] == "high"
    assert parsed["evidence_keys"] == ["a"]


def test_parse_insight_payload_rejects_bad_confidence():
    with pytest.raises(ValueError, match="confidence"):
        parse_insight_payload({"narrative": "x", "confidence": "certain", "evidence_keys": []})


def test_parse_critique_payload():
    payload = {"verdict": "partial", "reason": "missed the null_rate on author_handle"}
    assert parse_critique_payload(payload) == payload


def test_parse_critique_rejects_bad_verdict():
    with pytest.raises(ValueError, match="verdict"):
        parse_critique_payload({"verdict": "maybe", "reason": "x"})
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `pytest tests/test_llm_parsing.py -v`
Expected: FAIL — module not found.

- [ ] **Step 5.3: Write minimal implementation**

```python
# saturn/llm/parsing.py
"""Tolerant JSON extraction + schema validation for insight payloads."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OBJ = re.compile(r"\{.*\}", re.DOTALL)

_VALID_CONFIDENCE = {"high", "medium", "low"}
_VALID_VERDICT = {"agree", "disagree", "partial"}


def extract_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    fence = _FENCE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    obj_match = _OBJ.search(raw)
    if not obj_match:
        raise ValueError("no JSON object found in response")
    return json.loads(obj_match.group(0))


def parse_insight_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"narrative", "confidence", "evidence_keys"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"insight payload missing keys: {sorted(missing)}")
    if payload["confidence"] not in _VALID_CONFIDENCE:
        raise ValueError(f"invalid confidence: {payload['confidence']!r}")
    if not isinstance(payload["evidence_keys"], list):
        raise ValueError("evidence_keys must be a list")
    return {
        "narrative": str(payload["narrative"]),
        "confidence": payload["confidence"],
        "evidence_keys": [str(k) for k in payload["evidence_keys"]],
    }


def parse_critique_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"verdict", "reason"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"critique payload missing keys: {sorted(missing)}")
    if payload["verdict"] not in _VALID_VERDICT:
        raise ValueError(f"invalid verdict: {payload['verdict']!r}")
    return {"verdict": payload["verdict"], "reason": str(payload["reason"])}
```

- [ ] **Step 5.4: Run test to verify it passes**

Run: `pytest tests/test_llm_parsing.py -v`
Expected: 7 PASS.

- [ ] **Step 5.5: Commit**

```bash
git add saturn/llm/parsing.py tests/test_llm_parsing.py
git commit -m "feat: tolerant json extraction for llm payloads"
```

---

## Task 6: Engine — orchestrate provider → critic with fail-open behaviour

**Files:**
- Create: `saturn/llm/engine.py`
- Test: `tests/test_llm_engine.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_llm_engine.py
from unittest.mock import patch

from saturn.insights import InsightBundle
from saturn.llm.engine import run_insights
from saturn.llm.gateway import ProviderSpec
from saturn.profilers import Alert, ProfileResult
from saturn.report import assemble


def _mk_report():
    return assemble(
        source="hf://t/d", row_count=100, sampled_rows=100, seed=42,
        schema={"alt_text": "text"},
        results=[ProfileResult(column="alt_text", kind="text", n=100, n_null=0,
                               n_unique=90, stats={"len_mean": 200.0},
                               extras={}, alerts=[])],
        mode="full",
    )


def _fake_call(sequence):
    calls = iter(sequence)

    def _inner(spec, *, system, user, api_key, **kw):
        return next(calls)

    return _inner


def test_run_insights_single_provider_returns_dataset_and_column_insights():
    resp = ('{"narrative": "looks fine", "confidence": "high", "evidence_keys": ["n"]}',
            {"input_tokens": 10, "output_tokens": 5})
    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", side_effect=_fake_call([resp, resp])):
        bundle = run_insights(
            report,
            specs=[ProviderSpec("anthropic", "claude-sonnet-4-6")],
            api_keys={"anthropic": "sk-test"},
        )
    assert isinstance(bundle, InsightBundle)
    # One dataset-scope + one per-column insight
    scopes = [i.scope for i in bundle.insights]
    assert scopes.count("dataset") == 1
    assert scopes.count("column") == 1
    assert bundle.total_usage["input_tokens"] == 20
    assert bundle.errors == []


def test_run_insights_second_provider_critiques_first():
    primary = ('{"narrative": "all good", "confidence": "high", "evidence_keys": ["n"]}',
               {"input_tokens": 10, "output_tokens": 5})
    critic = ('{"verdict": "disagree", "reason": "mean length of 200 is atypical"}',
              {"input_tokens": 8, "output_tokens": 4})
    # dataset insight + dataset critique + column insight + column critique
    sequence = [primary, critic, primary, critic]
    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", side_effect=_fake_call(sequence)):
        bundle = run_insights(
            report,
            specs=[ProviderSpec("anthropic", "claude-sonnet-4-6"),
                   ProviderSpec("openai", "gpt-4o-mini")],
            api_keys={"anthropic": "sk-a", "openai": "sk-o"},
        )
    dataset_insight = next(i for i in bundle.insights if i.scope == "dataset")
    assert len(dataset_insight.critiques) == 1
    assert dataset_insight.critiques[0].verdict == "disagree"


def test_run_insights_fails_open_on_provider_error():
    def _boom(spec, *, system, user, api_key, **kw):
        raise RuntimeError("network down")

    report = _mk_report()
    with patch("saturn.llm.engine.call_provider", side_effect=_boom):
        bundle = run_insights(
            report,
            specs=[ProviderSpec("anthropic", "claude-sonnet-4-6")],
            api_keys={"anthropic": "sk"},
        )
    assert bundle.insights == []
    assert len(bundle.errors) >= 1
    assert "network down" in bundle.errors[0]["message"]
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `pytest tests/test_llm_engine.py -v`
Expected: FAIL — module not found.

- [ ] **Step 6.3: Write minimal implementation**

```python
# saturn/llm/engine.py
"""Insight pass orchestration.

Runs the primary provider over every column + the dataset. If a second provider
was requested it plays critic against the primary's insights.

Failures are fail-open: a provider error records an entry in `bundle.errors`
but does not abort. Saturn's deterministic output must never be blocked by an
optional LLM pass.
"""

from __future__ import annotations

from typing import Mapping

from ..insights import Critique, Insight, InsightBundle
from ..report import ReportData
from .evidence import column_evidence, dataset_evidence
from .gateway import ProviderSpec, call_provider
from .parsing import extract_json, parse_critique_payload, parse_insight_payload
from .prompts import (
    build_column_prompt,
    build_critique_prompt,
    build_dataset_prompt,
)


def _merge_usage(total: dict[str, int], usage: Mapping[str, int]) -> None:
    for k, v in usage.items():
        total[k] = total.get(k, 0) + int(v)


def _record_error(bundle: InsightBundle, where: str, exc: Exception) -> None:
    bundle.errors.append({"where": where, "type": type(exc).__name__, "message": str(exc)})


def _one_insight(
    bundle: InsightBundle,
    spec: ProviderSpec,
    api_key: str,
    system: str,
    user: str,
    *,
    scope: str,
    target: str,
) -> Insight | None:
    try:
        raw, usage = call_provider(spec, system=system, user=user, api_key=api_key)
        payload = parse_insight_payload(extract_json(raw))
    except Exception as e:
        _record_error(bundle, f"{scope}:{target}:{spec.label()}", e)
        return None
    _merge_usage(bundle.total_usage, usage)
    return Insight(
        scope=scope, target=target,
        narrative=payload["narrative"],
        confidence=payload["confidence"],
        evidence_keys=payload["evidence_keys"],
        model=spec.label(),
    )


def _one_critique(
    bundle: InsightBundle,
    spec: ProviderSpec,
    api_key: str,
    peer: Insight,
    evidence: dict,
) -> Critique | None:
    sys, user = build_critique_prompt(peer.to_dict(), evidence)
    try:
        raw, usage = call_provider(spec, system=sys, user=user, api_key=api_key)
        payload = parse_critique_payload(extract_json(raw))
    except Exception as e:
        _record_error(bundle, f"critique:{peer.scope}:{peer.target}:{spec.label()}", e)
        return None
    _merge_usage(bundle.total_usage, usage)
    return Critique(reviewer_model=spec.label(), verdict=payload["verdict"], reason=payload["reason"])


def run_insights(
    report: ReportData,
    *,
    specs: list[ProviderSpec],
    api_keys: Mapping[str, str],
) -> InsightBundle:
    if not specs:
        raise ValueError("at least one provider spec required")

    bundle = InsightBundle(providers=[s.label() for s in specs])
    primary = specs[0]
    critic = specs[1] if len(specs) >= 2 else None

    # Dataset-scope insight
    ds_ev = dataset_evidence(report)
    sys, user = build_dataset_prompt(ds_ev)
    ds_insight = _one_insight(
        bundle, primary, api_keys[primary.provider], sys, user,
        scope="dataset", target="__global__",
    )
    if ds_insight:
        bundle.insights.append(ds_insight)
        if critic:
            crit = _one_critique(bundle, critic, api_keys[critic.provider], ds_insight, ds_ev)
            if crit:
                ds_insight.critiques.append(crit)

    # Per-column insights
    for r in report.results:
        col_ev = column_evidence(report, r.column)
        sys, user = build_column_prompt(col_ev)
        col_insight = _one_insight(
            bundle, primary, api_keys[primary.provider], sys, user,
            scope="column", target=r.column,
        )
        if col_insight:
            bundle.insights.append(col_insight)
            if critic:
                crit = _one_critique(bundle, critic, api_keys[critic.provider], col_insight, col_ev)
                if crit:
                    col_insight.critiques.append(crit)

    return bundle
```

- [ ] **Step 6.4: Run test to verify it passes**

Run: `pytest tests/test_llm_engine.py -v`
Expected: 3 PASS.

- [ ] **Step 6.5: Commit**

```bash
git add saturn/llm/engine.py tests/test_llm_engine.py
git commit -m "feat: insight engine with fail-open provider + critic orchestration"
```

---

## Task 7: API-key resolution

**Files:**
- Create: `saturn/llm/keys.py`
- Test: `tests/test_llm_keys.py`

- [ ] **Step 7.1: Write the failing test**

```python
# tests/test_llm_keys.py
import os
from unittest.mock import patch

import pytest

from saturn.llm.keys import MissingKeyError, load_api_keys


def test_load_from_env_vars():
    env = {
        "ANTHROPIC_API_KEY": "sk-a",
        "OPENAI_API_KEY": "sk-o",
    }
    with patch.dict(os.environ, env, clear=False):
        keys = load_api_keys(["anthropic", "openai"])
    assert keys == {"anthropic": "sk-a", "openai": "sk-o"}


def test_load_raises_on_missing_key():
    env = {"ANTHROPIC_API_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        # Remove OPENAI_API_KEY from parent env if it exists
        os.environ.pop("OPENAI_API_KEY", None)
        with pytest.raises(MissingKeyError, match="openai"):
            load_api_keys(["anthropic", "openai"])
```

- [ ] **Step 7.2: Run test to verify it fails**

Run: `pytest tests/test_llm_keys.py -v`
Expected: FAIL — module not found.

- [ ] **Step 7.3: Write minimal implementation**

```python
# saturn/llm/keys.py
"""API key resolution.

Order: shared.config.ConfigManager (if importable) → env vars → fail.
"""

from __future__ import annotations

import os

_ENV_VAR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "xai": "XAI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "huggingface": "HF_TOKEN",
    "ollama": "OLLAMA_HOST",  # URL, not secret, but same contract
}


class MissingKeyError(RuntimeError):
    pass


def _from_config_manager(provider: str) -> str | None:
    try:
        from config import ConfigManager  # type: ignore
    except ImportError:
        return None
    try:
        cm = ConfigManager(app_name="saturn")
        return cm.get_api_key(provider) or None
    except Exception:
        return None


def load_api_keys(providers: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for p in providers:
        key = _from_config_manager(p)
        if not key:
            env_name = _ENV_VAR.get(p, f"{p.upper()}_API_KEY")
            key = os.environ.get(env_name) or None
        if not key:
            raise MissingKeyError(
                f"no API key for provider {p!r} (set {_ENV_VAR.get(p, p.upper()+'_API_KEY')} "
                f"or add to ~/documentation/API_KEYS.md)"
            )
        resolved[p] = key
    return resolved
```

- [ ] **Step 7.4: Run test to verify it passes**

Run: `pytest tests/test_llm_keys.py -v`
Expected: 2 PASS.

- [ ] **Step 7.5: Commit**

```bash
git add saturn/llm/keys.py tests/test_llm_keys.py
git commit -m "feat: api-key resolution (configmanager + env var fallback)"
```

---

## Task 8: Wire insights into `ReportData.to_findings()`

**Files:**
- Modify: `saturn/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 8.1: Write the failing test**

Append to `tests/test_report.py`:

```python
def test_to_findings_includes_insight_bundle_when_present():
    from saturn.insights import Insight, InsightBundle
    from saturn.profilers import ProfileResult
    from saturn.report import assemble

    results = [ProfileResult(column="x", kind="numeric", n=10, n_null=0,
                             n_unique=10, stats={}, extras={}, alerts=[])]
    report = assemble(source="s", row_count=10, sampled_rows=10, seed=0,
                      schema={"x": "numeric"}, results=results, mode="full")
    report.insight_bundle = InsightBundle(
        providers=["anthropic:claude-sonnet-4-6"],
        insights=[Insight(scope="dataset", target="__global__",
                          narrative="n", confidence="high",
                          evidence_keys=["row_count"],
                          model="anthropic:claude-sonnet-4-6")],
        total_usage={"input_tokens": 1, "output_tokens": 2},
    )
    findings = report.to_findings()
    assert "insights" in findings
    assert findings["insights"]["providers"] == ["anthropic:claude-sonnet-4-6"]
    assert findings["insights"]["total_usage"]["input_tokens"] == 1


def test_to_findings_omits_insights_key_when_bundle_absent():
    from saturn.profilers import ProfileResult
    from saturn.report import assemble

    results = [ProfileResult(column="x", kind="numeric", n=1, n_null=0, n_unique=1,
                             stats={}, extras={}, alerts=[])]
    report = assemble(source="s", row_count=1, sampled_rows=1, seed=0,
                      schema={"x": "numeric"}, results=results, mode="full")
    findings = report.to_findings()
    assert "insights" not in findings
```

- [ ] **Step 8.2: Run test to verify it fails**

Run: `pytest tests/test_report.py::test_to_findings_includes_insight_bundle_when_present -v`
Expected: FAIL — `ReportData` has no `insight_bundle` attribute.

- [ ] **Step 8.3: Modify `saturn/report.py`**

In the `ReportData` dataclass, add one field:

```python
# saturn/report.py — inside @dataclass class ReportData
    insight_bundle: "Optional[InsightBundle]" = None
```

(Add `from typing import Optional` if not already imported; forward-import `InsightBundle` inside `TYPE_CHECKING`.)

Top of file:

```python
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .insights import InsightBundle
```

In `to_findings(self)`, extend the return dict:

```python
    def to_findings(self) -> dict[str, Any]:
        out = {
            "saturn_version": __version__,
            "meta": { ... },                 # unchanged
            "schema": self.schema,
            "language_counts": self.language_counts,
            "notes": self.notes,
            "columns": [r.to_dict() for r in self.results],
        }
        if self.insight_bundle is not None:
            out["insights"] = self.insight_bundle.to_dict()
        return out
```

- [ ] **Step 8.4: Run test to verify it passes**

Run: `pytest tests/test_report.py -v`
Expected: all pass, including two new tests.

- [ ] **Step 8.5: Commit**

```bash
git add saturn/report.py tests/test_report.py
git commit -m "feat: thread InsightBundle through ReportData.to_findings"
```

---

## Task 9: Render insights in the HTML report

**Files:**
- Modify: `saturn/templates/report.html.j2`
- Modify: `saturn/report.py` (Jinja context)
- Test: `tests/test_report.py`

- [ ] **Step 9.1: Write the failing test**

```python
def test_render_html_includes_insight_narrative_when_present():
    from saturn.insights import Insight, InsightBundle
    from saturn.profilers import ProfileResult
    from saturn.report import assemble, render_html
    import tempfile
    from pathlib import Path

    results = [ProfileResult(column="alt_text", kind="text", n=10, n_null=0, n_unique=10,
                             stats={}, extras={}, alerts=[])]
    report = assemble(source="s", row_count=10, sampled_rows=10, seed=0,
                      schema={"alt_text": "text"}, results=results, mode="full")
    report.insight_bundle = InsightBundle(
        providers=["anthropic:claude-sonnet-4-6"],
        insights=[Insight(scope="column", target="alt_text",
                          narrative="DISTINCTIVE-NARRATIVE-STRING",
                          confidence="high", evidence_keys=[],
                          model="anthropic:claude-sonnet-4-6")],
    )
    with tempfile.TemporaryDirectory() as d:
        out = render_html(report, Path(d) / "r.html")
        html = out.read_text()
    assert "DISTINCTIVE-NARRATIVE-STRING" in html
    assert "anthropic:claude-sonnet-4-6" in html
```

- [ ] **Step 9.2: Run test to verify it fails**

Run: `pytest tests/test_report.py::test_render_html_includes_insight_narrative_when_present -v`
Expected: FAIL — narrative not in output.

- [ ] **Step 9.3: Add insights block to `saturn/templates/report.html.j2`**

Locate the per-column section and add an adjacent `<section aria-labelledby="insights-heading">` block rendered from `data.insight_bundle`. Template fragment:

```html+jinja
{% if data.insight_bundle %}
<section aria-labelledby="insights-heading" class="insights">
  <h2 id="insights-heading">Insights</h2>
  <p class="muted">Generated by: {{ data.insight_bundle.providers | join(", ") }}.
  These are opinions, not facts — the stats above are what saturn measured.</p>
  {% for ins in data.insight_bundle.insights %}
  <article class="insight" data-scope="{{ ins.scope }}" data-target="{{ ins.target }}">
    <header>
      <strong>{{ ins.target if ins.scope != "dataset" else "Dataset" }}</strong>
      <span class="badge conf-{{ ins.confidence }}">{{ ins.confidence }}</span>
      <span class="model">{{ ins.model }}</span>
    </header>
    <p>{{ ins.narrative }}</p>
    {% if ins.critiques %}
    <details>
      <summary>Critiques ({{ ins.critiques | length }})</summary>
      <ul>
      {% for c in ins.critiques %}
        <li><strong>{{ c.reviewer_model }}</strong>:
            <em>{{ c.verdict }}</em> — {{ c.reason }}</li>
      {% endfor %}
      </ul>
    </details>
    {% endif %}
  </article>
  {% endfor %}
</section>
{% endif %}
```

- [ ] **Step 9.4: Confirm the template receives `data.insight_bundle`**

`render_html` already passes `ReportData` as `data`, so `data.insight_bundle` resolves via attribute access. No change to `saturn/report.py` is required beyond confirming.

- [ ] **Step 9.5: Run test to verify it passes**

Run: `pytest tests/test_report.py -v`
Expected: all pass.

- [ ] **Step 9.6: Commit**

```bash
git add saturn/templates/report.html.j2 tests/test_report.py
git commit -m "feat: render insight bundle in html report (fail-closed when absent)"
```

---

## Task 10: CLI flags and terminal output

**Files:**
- Modify: `saturn/cli.py`
- Test: `tests/test_cli_llm.py` (new)

- [ ] **Step 10.1: Write the failing test**

```python
# tests/test_cli_llm.py
from unittest.mock import patch

from typer.testing import CliRunner

from saturn.cli import app
from saturn.insights import Insight, InsightBundle


def _fake_bundle():
    return InsightBundle(
        providers=["anthropic:claude-sonnet-4-6"],
        insights=[Insight(scope="dataset", target="__global__", narrative="n",
                          confidence="high", evidence_keys=[],
                          model="anthropic:claude-sonnet-4-6")],
        total_usage={"input_tokens": 5, "output_tokens": 3},
    )


def test_analyze_with_llm_flag_invokes_engine(tmp_path):
    runner = CliRunner()
    out = tmp_path / "r.html"
    findings = tmp_path / "r.json"

    # Point at a tiny local CSV so we don't touch HF.
    csv = tmp_path / "t.csv"
    csv.write_text("a,b\n1,x\n2,y\n3,z\n")

    with patch("saturn.cli.run_insights", return_value=_fake_bundle()) as mock_engine, \
         patch("saturn.cli.load_api_keys", return_value={"anthropic": "sk"}):
        result = runner.invoke(app, [
            "analyze", str(csv),
            "--out", str(out), "--findings", str(findings),
            "--llm", "anthropic:claude-sonnet-4-6",
        ])
    assert result.exit_code == 0, result.output
    mock_engine.assert_called_once()
    assert "insights" in findings.read_text()
```

- [ ] **Step 10.2: Run test to verify it fails**

Run: `pytest tests/test_cli_llm.py -v`
Expected: FAIL — `--llm` option not recognised.

- [ ] **Step 10.3: Modify `saturn/cli.py`**

Top of file, add imports:

```python
from .insights import InsightBundle
from .llm.engine import run_insights
from .llm.gateway import parse_provider_spec
from .llm.keys import load_api_keys, MissingKeyError
```

Extend `_run_full` and `_run_sampled` signatures with `llm_spec: list[str] | None` and, after `assemble(...)`, before `render_html`:

```python
    if llm_spec:
        try:
            specs = [parse_provider_spec(s) for s in llm_spec]
            keys = load_api_keys([s.provider for s in specs])
            with console.status(f"insight pass ({', '.join(s.label() for s in specs)})", spinner="dots"):
                data.insight_bundle = run_insights(data, specs=specs, api_keys=keys)
            if data.insight_bundle.errors:
                console.print(f"[yellow]insight pass completed with {len(data.insight_bundle.errors)} error(s)[/]")
            else:
                console.print(f"[green]✓[/] insight pass: {len(data.insight_bundle.insights)} insights")
        except MissingKeyError as e:
            console.print(f"[red]insight pass skipped:[/] {e}")
```

Add the Typer option to `analyze`, `huggingface`, and `compare`:

```python
    llm_spec: list[str] = typer.Option(
        None, "--llm",
        help="provider[:model] to run insight pass. Repeat for primary + critic. "
             "Example: --llm anthropic --llm openai:gpt-4o-mini",
    ),
```

Pass through into `_run_full` / `_run_sampled`.

- [ ] **Step 10.4: Run test to verify it passes**

Run: `pytest tests/test_cli_llm.py -v`
Expected: PASS.

- [ ] **Step 10.5: Run the full suite**

```bash
SATURN_NO_NETWORK=1 pytest -q
```
Expected: all pass.

- [ ] **Step 10.6: Commit**

```bash
git add saturn/cli.py tests/test_cli_llm.py
git commit -m "feat: --llm flag wires insight pass through analyze/huggingface/compare"
```

---

## Task 11: Compare-mode insight pass

**Files:**
- Modify: `saturn/llm/engine.py` — add `run_compare_insights(report: CompareReport, …)`
- Modify: `saturn/compare.py` — add `insight_bundle` field to `CompareReport`, include in `to_dict()`
- Modify: `saturn/templates/compare.html.j2` — render insights section
- Modify: `saturn/cli.py` — wire `--llm` into the `compare` command

**Why it's separate:** the shape of compare evidence is different (pairs of per-column stats + deltas), so its prompt builder and tests stand on their own.

- [ ] **Step 11.1: Write failing tests** — analogous to Task 10 but using the `compare` CLI command with a local CSV split by `--by`. Cover: bundle attaches to `CompareReport`, HTML includes narrative, findings JSON includes `insights` key.

- [ ] **Step 11.2: Extend prompts**

Add `build_compare_column_prompt(delta: dict, a_ev: dict, b_ev: dict)` and `build_compare_summary_prompt(report_summary: dict)` to `saturn/llm/prompts.py`.

- [ ] **Step 11.3: Add `run_compare_insights`** to `engine.py` — mirrors `run_insights` but walks the divergence-ranked columns from `report.divergence_summary()` and builds pair-evidence.

- [ ] **Step 11.4: Thread through `CompareReport.to_dict` and the compare template.**

- [ ] **Step 11.5: Run full suite + commit**

```bash
pytest -q
git add -u
git commit -m "feat: compare-mode insight pass"
```

---

## Task 12: Integration test — end-to-end with a recorded provider

**Files:**
- Create: `tests/test_llm_integration.py`
- Create: `tests/fixtures/insight_replay/*.json`

**Why:** every unit test mocks `call_provider`. One integration test replays real recorded responses through the full pipeline to catch wiring bugs the mocks hide. Run only when `SATURN_LIVE_LLM=1`.

- [ ] **Step 12.1: Record once** (developer, not CI)

```bash
SATURN_RECORD_LLM=1 python -m saturn.cli analyze tests/fixtures/tiny.csv --llm anthropic
# stash responses into tests/fixtures/insight_replay/
```

(Implement a simple `SATURN_RECORD_LLM` env-var hook in `gateway.call_provider` that writes request+response JSON to a cassette dir when the env var is set. Replay honours `SATURN_REPLAY_LLM=<dir>`.)

- [ ] **Step 12.2: Write test that replays cassette and checks final HTML + JSON**

```python
@pytest.mark.skipif(not os.environ.get("SATURN_REPLAY_LLM"),
                    reason="set SATURN_REPLAY_LLM=tests/fixtures/insight_replay to run")
def test_end_to_end_replay_produces_insights(tmp_path):
    ...
```

- [ ] **Step 12.3: Commit**

```bash
git add -u
git commit -m "test: replay-based integration test for insight pass"
```

---

## Task 13: Docs + README

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` (status section)

- [ ] **Step 13.1: Add `--llm` section to README** with one worked example and a clear statement that the deterministic pass is unchanged.

- [ ] **Step 13.2: Flip CLAUDE.md roadmap** — move Phase 2 from 🔜 to ✅, keep Phase 3/5 as planned.

- [ ] **Step 13.3: Commit + merge**

```bash
git add README.md CLAUDE.md
git commit -m "docs: phase 2 llm insight pass shipped"
git checkout main
git merge --no-ff phase2-llm-insight
```

---

## Self-review notes

- **Spec coverage:** every bullet in the README roadmap for Phase 2 (multi-provider, catfish critic, opt-in, never blocks deterministic output) has a task. ✓
- **No placeholders:** every code step shows the code. Task 11 uses "analogous to" wording for brevity on sub-steps — expand inline at execution time if the executor isn't comfortable re-deriving the shape.
- **Type consistency:** `Insight`, `Critique`, `InsightBundle`, `ProviderSpec` names are stable from Task 1 onward. `run_insights` / `run_compare_insights` are the two engine entry points.
- **Fail-open guarantee:** Task 6 test `test_run_insights_fails_open_on_provider_error` pins the contract. Task 10's CLI catches `MissingKeyError` and logs, never raises to the user.

## Non-goals (explicit, do not implement in this plan)

- Streaming model output to the terminal. `--llm` blocks until the bundle is complete. Streaming belongs in Phase 5's web viewer.
- Cost estimation / token budgeting upfront. `total_usage` reports what was used; pre-flight budgeting is a separate slice.
- Tool-use or function calling. The prompt contract is JSON-only. Keeps the parsing surface minimal.
- BERTopic / embedding-based clustering. That is Phase 3 — a different plan.
