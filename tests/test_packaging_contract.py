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


def test_release_launcher_uses_external_commit_venv_and_exact_manifest():
    launcher = (ROOT / "scripts/start.sh").read_text()
    assert 'SATURN_DEPLOY_MANIFEST="$APP_DIR/.saturn-deployment.json"' in launcher
    assert 'VENV_DIR="$DEPLOY_ROOT/venvs/$DEPLOY_COMMIT"' in launcher
    assert 'source "$VENV_DIR/bin/activate"' in launcher


def test_service_runs_launcher_through_atomic_current_link():
    service = (ROOT / "deploy/saturn-viewer.service").read_text()
    assert "ExecStart=/home/coolhand/projects/saturn/current/scripts/start.sh" in service
    assert "WorkingDirectory=/home/coolhand/projects/saturn" in service


def test_release_script_uses_fresh_stages_and_atomic_activation():
    script = (ROOT / "scripts/deploy-release.sh").read_text()
    assert 'mktemp -d "$RELEASES/' in script
    assert 'mktemp -d "$VENVS/' in script
    assert 'git -C "$REPO" archive "$COMMIT"' in script
    assert 'record "$COMMIT" "$STAGE"' in script
    assert 'verify "$STAGE"' in script
    assert 'python3 -m venv "$VENV_STAGE"' in script
    assert 'chmod -R a-w "$RELEASE" "$VENV"' in script
    assert 'activate "$COMMIT" "$DEPLOY_ROOT"' in script
