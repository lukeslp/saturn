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
