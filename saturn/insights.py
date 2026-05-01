"""Dataclasses for LLM-generated insights and their cross-model critiques.

Separate module from `saturn/llm/` so it can be imported by `saturn.report`
without pulling the provider gateway into the deterministic stats path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Critique:
    reviewer_model: str
    verdict: str  # "agree" | "disagree" | "partial"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Insight:
    scope: str          # "column" | "dataset" | "compare"
    target: str         # column name, or "__global__"
    narrative: str
    confidence: str     # "high" | "medium" | "low"
    evidence_keys: list[str]
    model: str          # "provider:model_id"
    critiques: list[Critique] = field(default_factory=list)

    # Column-scope curation fields (None on dataset-scope insights):
    # - `role` is one of identifier | label | feature | metadata | free_text |
    #   timestamp | numeric_target | foreign_key | other. Drives a chip on
    #   the column heading and changes the default treatment recommendation.
    # - `treatment` is a one-line suggestion the model gives for handling
    #   this column downstream ("normalize before modeling", "drop", "tokenize").
    role: str | None = None
    treatment: str | None = None

    # Dataset-scope curation field (empty on column-scope insights). Each item
    # is `{"column": str, "kind": str, "caption": str}`. The viewer promotes
    # these to a "featured charts" rail so non-experts know what to look at first.
    featured_charts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "scope": self.scope,
            "target": self.target,
            "narrative": self.narrative,
            "confidence": self.confidence,
            "evidence_keys": self.evidence_keys,
            "model": self.model,
            "critiques": [c.to_dict() for c in self.critiques],
        }
        if self.role is not None:
            out["role"] = self.role
        if self.treatment is not None:
            out["treatment"] = self.treatment
        if self.featured_charts:
            out["featured_charts"] = self.featured_charts
        return out


@dataclass
class InsightBundle:
    providers: list[str]
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
