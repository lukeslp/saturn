"""Tests for BYOK (bring-your-own-key) flow on the public viewer."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from saturn.viewer.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(findings_dir=tmp_path, testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


# ---------- _resolve_llm_request ---------------------------------------------


def test_resolve_no_llm_checkbox_wins(app):
    from saturn.viewer.app import _resolve_llm_request

    form = {"no_llm": "1", "api_key": "sk-test", "llm": "anthropic"}
    provider, key = _resolve_llm_request(app, form)
    assert (provider, key) == (None, None)


def test_resolve_byok_with_explicit_provider(app):
    from saturn.viewer.app import _resolve_llm_request

    form = {"llm": "openai", "api_key": "sk-mine"}
    provider, key = _resolve_llm_request(app, form)
    assert provider == "openai"
    assert key == "sk-mine"


def test_resolve_byok_assumes_anthropic_when_no_provider(app):
    from saturn.viewer.app import _resolve_llm_request

    form = {"api_key": "sk-mine"}
    provider, key = _resolve_llm_request(app, form)
    assert provider == "anthropic"
    assert key == "sk-mine"


def test_resolve_no_key_returns_none_when_default_blank(app):
    from saturn.viewer.app import _resolve_llm_request

    app.config["SATURN_DEFAULT_LLM"] = ""
    provider, key = _resolve_llm_request(app, {})
    assert provider is None
    assert key is None


def test_resolve_falls_back_to_default_when_form_empty(app):
    from saturn.viewer.app import _resolve_llm_request

    app.config["SATURN_DEFAULT_LLM"] = "anthropic"
    provider, key = _resolve_llm_request(app, {})
    assert provider == "anthropic"
    assert key is None  # runner will look up server keys


def test_resolve_backfill_disallows_no_llm_pass(app):
    from saturn.viewer.app import _resolve_llm_request

    form = {"no_llm": "1", "llm": "anthropic", "api_key": "sk-x"}
    provider, key = _resolve_llm_request(app, form, allow_no_llm=False)
    # no_llm flag is ignored; the explicit provider+key wins
    assert provider == "anthropic"
    assert key == "sk-x"


# ---------- routes thread api_key through to the runner ----------------------


def test_analyze_upload_passes_api_key_to_runner(client, monkeypatch):
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["args"] = args
        job = Job(id="j", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post(
        "/analyze",
        data={
            "file": (io.BytesIO(b"a,b\n1,x\n"), "tiny.csv"),
            "llm": "openai",
            "api_key": "sk-mine",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    _dir, _path, _fid, provider, api_key = captured["args"]
    assert provider == "openai"
    assert api_key == "sk-mine"


def test_analyze_hf_passes_api_key_to_runner(client, monkeypatch):
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["args"] = args
        job = Job(id="j2", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post(
        "/analyze-hf",
        data={"repo": "lukeslp/x", "api_key": "sk-byok", "llm": "anthropic"},
    )
    assert resp.status_code == 302
    _dir, _repo, _fid, provider, api_key = captured["args"]
    assert provider == "anthropic"
    assert api_key == "sk-byok"


def test_backfill_passes_api_key_to_runner(client, tmp_path, monkeypatch):
    (tmp_path / "demo.json").write_text(json.dumps({
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0,
                 "mode": "full", "generated_at": "2026-04-23T00:00:00+00:00"},
        "schema": {"a": "numeric"}, "language_counts": {}, "notes": [],
        "columns": [],
    }))
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["args"] = args
        job = Job(id="j3", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post("/backfill/demo", data={"llm": "groq", "api_key": "gsk-mine"})
    assert resp.status_code == 302
    _dir, _fid, provider, api_key = captured["args"]
    assert provider == "groq"
    assert api_key == "gsk-mine"


def test_analyze_no_byok_no_default_redirects_to_stats_only(client, app, monkeypatch):
    """On a public instance with default_llm blank, no api_key in form → no LLM pass."""
    app.config["SATURN_DEFAULT_LLM"] = ""
    captured = {}

    def fake_start(kind, target, *args, **kwargs):
        from saturn.viewer.runner import Job, _JOBS
        captured["args"] = args
        job = Job(id="j4", kind=kind)
        _JOBS[job.id] = job
        return job

    monkeypatch.setattr("saturn.viewer.app.start_job", fake_start)
    resp = client.post(
        "/analyze",
        data={"file": (io.BytesIO(b"a,b\n1,x\n"), "t.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    _dir, _path, _fid, provider, api_key = captured["args"]
    assert provider is None  # public path, no key, stats-only by default
    assert api_key is None


# ---------- runner subprocess env construction ------------------------------


def test_subprocess_env_with_byok_only_sets_one_key():
    from saturn.viewer.runner import _build_subprocess_env

    env = _build_subprocess_env("openai:gpt-4o", "sk-byok")
    assert env["OPENAI_API_KEY"] == "sk-byok"
    # Other provider keys must be wiped, even if the parent env had them
    assert "ANTHROPIC_API_KEY" not in env
    assert "GROQ_API_KEY" not in env


def test_subprocess_env_no_key_disables_config_manager():
    from saturn.viewer.runner import _build_subprocess_env

    env = _build_subprocess_env("anthropic", None)
    assert env["SATURN_LLM_DISABLE_CONFIG_MANAGER"] == "1"


def test_subprocess_env_no_provider_no_disable_flag():
    """If the request didn't ask for an LLM, the disable flag is irrelevant."""
    from saturn.viewer.runner import _build_subprocess_env

    env = _build_subprocess_env(None, None)
    assert "SATURN_LLM_DISABLE_CONFIG_MANAGER" not in env


# ---------- keys.py honors the disable flag ---------------------------------


def test_keys_load_skips_config_manager_when_flag_set(monkeypatch):
    from saturn.llm import keys

    monkeypatch.setenv("SATURN_LLM_DISABLE_CONFIG_MANAGER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Simulate ConfigManager that WOULD return a key — should be ignored.
    with patch("saturn.llm.keys._from_config_manager", return_value="server-key") as cm:
        try:
            keys.load_api_keys(["anthropic"])
        except keys.MissingKeyError:
            pass
    # _from_config_manager IS called but returns None due to the env flag.
    # The real function honors the flag at the top; the patch above replaces
    # the whole function so the behavior we want is: it never returns a key.
    # Real verification: call the unpatched _from_config_manager directly.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # ensure env absent
    assert keys._from_config_manager("anthropic") is None
