from importlib.metadata import version
from pathlib import Path

import saturn


ROOT = Path(__file__).parents[1]


def test_runtime_version_comes_from_installed_metadata():
    assert saturn.__version__ == version("saturn-dissect")


def test_web_extra_declares_runtime_and_test_dependencies():
    pyproject = (ROOT / "pyproject.toml").read_text()
    for requirement in ('"gunicorn', '"markdown', '"bleach'):
        assert requirement in pyproject


def test_gateway_has_no_private_shared_dependency():
    gateway = (ROOT / "saturn/llm/gateway.py").read_text()
    assert "llm_providers" not in gateway
    assert "~/shared" not in gateway


def test_llm_extra_requires_patched_litellm_floor():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert '"litellm>=1.84.0"' in pyproject
