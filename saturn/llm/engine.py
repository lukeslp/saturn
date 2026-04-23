"""Insight pass orchestration.

Runs the primary provider over each column + the dataset. If a second provider
was requested, it plays critic against the primary's insights (the 'catfish').

Fail-open: a provider error records an entry in `bundle.errors` but does not
abort. Saturn's deterministic output must never be blocked by the optional LLM
pass.
"""

from __future__ import annotations

from typing import Mapping

from ..insights import Critique, Insight, InsightBundle
from ..report import ReportData
from .evidence import column_evidence, dataset_evidence
from .gateway import ProviderSpec, call_provider
from .parsing import (
    extract_json,
    parse_critique_payload,
    parse_insight_payload,
)
from .prompts import (
    build_column_prompt,
    build_critique_prompt,
    build_dataset_prompt,
)


def _merge_usage(total: dict[str, int], usage: Mapping[str, int]) -> None:
    for k, v in usage.items():
        total[k] = total.get(k, 0) + int(v)


def _record_error(bundle: InsightBundle, where: str, exc: Exception) -> None:
    bundle.errors.append(
        {"where": where, "type": type(exc).__name__, "message": str(exc)}
    )


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
        scope=scope,
        target=target,
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
    return Critique(
        reviewer_model=spec.label(),
        verdict=payload["verdict"],
        reason=payload["reason"],
    )


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
        bundle,
        primary,
        api_keys[primary.provider],
        sys,
        user,
        scope="dataset",
        target="__global__",
    )
    if ds_insight:
        bundle.insights.append(ds_insight)
        if critic:
            crit = _one_critique(
                bundle, critic, api_keys[critic.provider], ds_insight, ds_ev
            )
            if crit:
                ds_insight.critiques.append(crit)

    # Per-column insights
    for r in report.results:
        col_ev = column_evidence(report, r.column)
        sys, user = build_column_prompt(col_ev)
        col_insight = _one_insight(
            bundle,
            primary,
            api_keys[primary.provider],
            sys,
            user,
            scope="column",
            target=r.column,
        )
        if col_insight:
            bundle.insights.append(col_insight)
            if critic:
                crit = _one_critique(
                    bundle, critic, api_keys[critic.provider], col_insight, col_ev
                )
                if crit:
                    col_insight.critiques.append(crit)

    return bundle
