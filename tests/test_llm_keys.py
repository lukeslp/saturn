"""Tests for saturn.llm.keys — API key resolution."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from saturn.llm.keys import MissingKeyError, load_api_keys


def _clear_envs(*names):
    for n in names:
        os.environ.pop(n, None)


def test_load_from_env_vars():
    _clear_envs("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
    env = {"ANTHROPIC_API_KEY": "sk-a", "OPENAI_API_KEY": "sk-o"}
    # Also block ConfigManager from intervening
    with patch("saturn.llm.keys._from_config_manager", return_value=None), \
         patch.dict(os.environ, env, clear=False):
        keys = load_api_keys(["anthropic", "openai"])
    assert keys == {"anthropic": "sk-a", "openai": "sk-o"}


def test_load_raises_on_missing_key():
    _clear_envs("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    with patch("saturn.llm.keys._from_config_manager", return_value=None), \
         patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-a"}, clear=False):
        with pytest.raises(MissingKeyError, match="openai"):
            load_api_keys(["anthropic", "openai"])


def test_config_manager_takes_precedence_over_env():
    with patch("saturn.llm.keys._from_config_manager", return_value="from-cm"), \
         patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-env"}):
        keys = load_api_keys(["anthropic"])
    assert keys["anthropic"] == "from-cm"


def test_hf_uses_hf_token_env_var():
    _clear_envs("HF_TOKEN")
    with patch("saturn.llm.keys._from_config_manager", return_value=None), \
         patch.dict(os.environ, {"HF_TOKEN": "hf-x"}, clear=False):
        keys = load_api_keys(["huggingface"])
    assert keys["huggingface"] == "hf-x"
