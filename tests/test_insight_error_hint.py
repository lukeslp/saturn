"""Tests for the `insight_error_hint` macro on the missing-summary form.

When a previous LLM pass failed, we surface a redacted plain-language hint
above the "Generate summary" form so the researcher knows whether to retry
or paste their own key. This file checks that:

  1. No errors → no hint
  2. Each known error type → its specific hint
  3. Raw provider error message is NOT leaked verbatim
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturn.viewer.app import create_app


def _findings(insights_block: dict | None = None) -> dict:
    return {
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 100, "sampled_rows": 100, "seed": 0,
                 "mode": "full", "generated_at": "2026-05-01T00:00:00+00:00"},
        "schema": {"a": "numeric"},
        "language_counts": {},
        "notes": [],
        "columns": [{
            "column": "a", "kind": "numeric",
            "n": 100, "n_null": 0, "n_unique": 50,
            "stats": {"median": 5.0},
            "extras": {},
            "alerts": [], "null_rate": 0.0,
        }],
        **({"insights": insights_block} if insights_block is not None else {}),
    }


@pytest.fixture
def client(tmp_path: Path):
    return create_app(findings_dir=tmp_path, testing=True).test_client(), tmp_path


def _write(client_pair, payload: dict, finding_id: str = "demo") -> str:
    _, tmp_path = client_pair
    (tmp_path / f"{finding_id}.json").write_text(json.dumps(payload))
    return finding_id


# ---------- macro behavior --------------------------------------------------


def test_no_hint_when_no_insights_block(client):
    cli, _ = client
    fid = _write(client, _findings())
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "last attempt failed" not in body.lower()


def test_no_hint_when_insights_succeeded(client):
    cli, _ = client
    fid = _write(client, _findings({
        "providers": ["anthropic"],
        "insights": [{"scope": "dataset", "target": "__global__",
                       "narrative": "ok", "confidence": "medium",
                       "model": "anthropic:claude-opus-4-7"}],
        "errors": [],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "last attempt failed" not in body.lower()


def test_hint_for_credit_balance_failure(client):
    """The exact failure shape we hit during the 2026-05-01 batch run."""
    cli, _ = client
    fid = _write(client, _findings({
        "providers": ["anthropic:claude-opus-4-7"],
        "insights": [],
        "errors": [{
            "where": "dataset:__global__:anthropic:claude-opus-4-7",
            "type": "BadRequestError",
            "message": "Error code: 400 - {'message': 'Your credit balance is too low'}",
        }],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "out of credits" in body.lower()
    # Form is still shown so user can paste their own key
    assert "Generate summary" in body


def test_hint_for_rate_limit_failure(client):
    cli, _ = client
    fid = _write(client, _findings({
        "providers": ["anthropic"],
        "insights": [],
        "errors": [{"type": "RateLimitError",
                     "message": "Error 429: rate limit exceeded"}],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "rate-limited" in body.lower()


def test_hint_for_auth_failure(client):
    cli, _ = client
    fid = _write(client, _findings({
        "providers": ["openai"],
        "insights": [],
        "errors": [{"type": "AuthenticationError",
                     "message": "Invalid API key"}],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "rejected the key" in body.lower()


def test_hint_for_timeout_failure(client):
    cli, _ = client
    fid = _write(client, _findings({
        "providers": ["anthropic"],
        "insights": [],
        "errors": [{"type": "TimeoutError",
                     "message": "Request timed out after 60s"}],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "timed out" in body.lower()


def test_hint_for_unknown_failure_uses_generic_phrasing(client):
    cli, _ = client
    fid = _write(client, _findings({
        "providers": ["anthropic"],
        "insights": [],
        "errors": [{"type": "WeirdError", "message": "something went sideways"}],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "previous attempt failed" in body.lower()
    assert "WeirdError" in body


def test_raw_provider_message_is_not_leaked(client):
    """The raw error message body — which can contain account-identifying
    URLs or sensitive details — must NOT appear verbatim in the page."""
    cli, _ = client
    raw_msg = "Error code: 400 - go to https://console.anthropic.com/settings/plans/secret-account-id-12345"
    fid = _write(client, _findings({
        "providers": ["anthropic"],
        "insights": [],
        "errors": [{"type": "BadRequestError", "message": raw_msg}],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "secret-account-id-12345" not in body
    assert "console.anthropic.com" not in body


def test_hint_uses_last_error_when_multiple(client):
    """If many errors recorded (per-column failures), we summarise the latest."""
    cli, _ = client
    fid = _write(client, _findings({
        "providers": ["anthropic"],
        "insights": [],
        "errors": [
            {"type": "RateLimitError", "message": "429 rate limit"},
            {"type": "BadRequestError",
             "message": "400 - your credit balance is too low"},
        ],
    }))
    body = cli.get(f"/view/{fid}").get_data(as_text=True)
    assert "out of credits" in body.lower()
    assert "rate-limited" not in body.lower()


# ---------- compare-mode also surfaces the hint -----------------------------


def _compare_findings(insights_block: dict | None = None) -> dict:
    return {
        "saturn_version": "0.2.0",
        "a": {"label": "A", "source": "s", "row_count": 10,
              "schema": {"a": "numeric"}, "language_counts": {}},
        "b": {"label": "B", "source": "s", "row_count": 10,
              "schema": {"a": "numeric"}, "language_counts": {}},
        "columns": [{"column": "a", "kind": "numeric",
                      "a": None, "b": None, "delta": {}, "notes": []}],
        "divergences": [],
        "generated_at": "2026-05-01T00:00:00+00:00",
        **({"insights": insights_block} if insights_block is not None else {}),
    }


def test_compare_view_surfaces_hint_too(client):
    cli, tmp_path = client
    (tmp_path / "cmp.json").write_text(json.dumps(_compare_findings({
        "providers": ["anthropic"],
        "insights": [],
        "errors": [{"type": "BadRequestError",
                     "message": "credit balance is too low"}],
    })))
    body = cli.get("/view/cmp").get_data(as_text=True)
    assert "out of credits" in body.lower()
    assert "Generate compare summary" in body
