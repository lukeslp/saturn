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


def test_resolve_byok_assumes_openai_luna_when_no_provider(app):
    from saturn.viewer.app import _resolve_llm_request

    form = {"api_key": "sk-mine"}
    provider, key = _resolve_llm_request(app, form)
    assert provider == "openai:gpt-5.6-luna"
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


def test_resolve_ollama_keyless_uses_local_sentinel(app):
    """Ollama with empty key gets the 'local' sentinel so the gateway
    doesn't raise MissingKeyError; the provider class falls through to
    its OLLAMA_HOST default."""
    from saturn.viewer.app import _resolve_llm_request

    form = {"llm": "ollama"}
    provider, key = _resolve_llm_request(app, form)
    assert provider == "ollama"
    assert key == "local"


def test_resolve_ollama_with_explicit_bearer_passes_through(app):
    from saturn.viewer.app import _resolve_llm_request

    form = {"llm": "ollama", "api_key": "real-bearer-token"}
    provider, key = _resolve_llm_request(app, form)
    assert provider == "ollama"
    assert key == "real-bearer-token"


def test_resolve_ollama_with_model_spec_keyless(app):
    """`ollama:llama3.2` should still get the local sentinel."""
    from saturn.viewer.app import _resolve_llm_request

    form = {"llm": "ollama:llama3.2"}
    provider, key = _resolve_llm_request(app, form)
    assert provider == "ollama:llama3.2"
    assert key == "local"


# ---------- subprocess env construction --------------------------------------


def test_build_subprocess_env_skips_ollama_local_sentinel():
    """OLLAMA_API_KEY=local would make the provider send a literal
    'Bearer local' header — which an unauthenticated localhost ollama
    rejects. We avoid setting it."""
    from saturn.viewer.runner import _build_subprocess_env

    env = _build_subprocess_env("ollama", "local")
    assert env.get("OLLAMA_API_KEY") is None


def test_build_subprocess_env_sets_ollama_bearer_for_real_token():
    """A non-'local' value is treated as a Bearer token for hosted ollama."""
    from saturn.viewer.runner import _build_subprocess_env

    env = _build_subprocess_env("ollama", "real-token-xyz")
    assert env.get("OLLAMA_API_KEY") == "real-token-xyz"


def test_load_api_keys_ollama_keyless_returns_local_sentinel(monkeypatch):
    """`load_api_keys(['ollama'])` with no env at all should NOT raise —
    ollama is special-cased to default to the 'local' sentinel."""
    from saturn.llm.keys import load_api_keys

    # Scrub any existing OLLAMA_HOST so this is a true keyless test
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    # Bypass ConfigManager too
    monkeypatch.setenv("SATURN_LLM_DISABLE_CONFIG_MANAGER", "1")

    keys = load_api_keys(["ollama"])
    assert keys == {"ollama": "local"}


def test_load_api_keys_ollama_honors_explicit_host(monkeypatch):
    from saturn.llm.keys import load_api_keys

    monkeypatch.setenv("OLLAMA_HOST", "http://my-ollama:11434")
    monkeypatch.setenv("SATURN_LLM_DISABLE_CONFIG_MANAGER", "1")
    keys = load_api_keys(["ollama"])
    assert keys == {"ollama": "http://my-ollama:11434"}


def test_load_api_keys_other_providers_still_raise_when_missing(monkeypatch):
    """The ollama keyless special case must NOT leak to other providers."""
    from saturn.llm.keys import MissingKeyError, load_api_keys

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SATURN_LLM_DISABLE_CONFIG_MANAGER", "1")
    with pytest.raises(MissingKeyError, match="anthropic"):
        load_api_keys(["anthropic"])


# ---------- demo-mode env: SATURN_PUBLIC_KEYS=1 ------------------------------


def test_demo_mode_does_not_disable_config_manager(monkeypatch):
    """When SATURN_PUBLIC_KEYS=1, anonymous uploads silently use the
    server's keys instead of being scrubbed and bypassed."""
    from saturn.viewer.runner import _build_subprocess_env

    monkeypatch.setenv("SATURN_PUBLIC_KEYS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key-xyz")
    env = _build_subprocess_env("anthropic", None)
    # Server key is preserved
    assert env.get("ANTHROPIC_API_KEY") == "server-key-xyz"
    # ConfigManager fallback is NOT disabled
    assert env.get("SATURN_LLM_DISABLE_CONFIG_MANAGER") != "1"


def test_demo_mode_off_still_scrubs_no_byok_uploads(monkeypatch):
    """Default (private) posture: no BYOK + no demo flag = key scrubbed."""
    from saturn.viewer.runner import _build_subprocess_env

    monkeypatch.delenv("SATURN_PUBLIC_KEYS", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key-xyz")
    env = _build_subprocess_env("anthropic", None)
    assert env.get("ANTHROPIC_API_KEY") is None
    assert env.get("SATURN_LLM_DISABLE_CONFIG_MANAGER") == "1"


def test_byok_still_isolates_in_demo_mode(monkeypatch):
    """Even in demo mode, an explicit BYOK key isolates that provider's
    key only — server keys for other providers are scrubbed."""
    from saturn.viewer.runner import _build_subprocess_env

    monkeypatch.setenv("SATURN_PUBLIC_KEYS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "server-openai")
    env = _build_subprocess_env("openai", "user-supplied-openai-key")
    assert env.get("OPENAI_API_KEY") == "user-supplied-openai-key"
    assert env.get("ANTHROPIC_API_KEY") is None  # scrubbed


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


def test_backfill_replaces_findings_atomically(tmp_path, monkeypatch):
    from saturn.insights import InsightBundle
    from saturn.viewer.runner import backfill_insights

    path = tmp_path / "demo.json"
    path.write_text(json.dumps({
        "saturn_version": "0.2.0",
        "meta": {"source": "s", "row_count": 1, "sampled_rows": 1, "seed": 0,
                 "mode": "full", "generated_at": "2026-04-23T00:00:00+00:00"},
        "schema": {}, "language_counts": {}, "notes": [], "columns": [],
    }))
    monkeypatch.setattr(
        "saturn.llm.engine.run_insights",
        lambda *args, **kwargs: InsightBundle(providers=["groq:test"]),
    )
    calls = []
    from saturn import report as report_module
    real_atomic_write = report_module._atomic_write_text

    def recording_atomic_write(output, content):
        calls.append(Path(output))
        real_atomic_write(output, content)

    monkeypatch.setattr(report_module, "_atomic_write_text", recording_atomic_write)

    backfill_insights("job", tmp_path, "demo", "groq:test", api_key="gsk-test")

    assert calls == [path]
    assert json.loads(path.read_text())["insights"]["providers"] == ["groq:test"]


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
