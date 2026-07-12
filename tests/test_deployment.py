from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from saturn.deployment import (
    activate_release,
    create_manifest,
    record_git_deployment,
    verify_manifest,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_records_commit_and_file_hashes(tmp_path):
    (tmp_path / "saturn").mkdir()
    source = tmp_path / "saturn" / "app.py"
    source.write_text("print('saturn')\n")

    manifest = create_manifest(
        tmp_path,
        commit="49d8196e990bb08cb25c35feef9bb2f3c8a92910",
        paths=["saturn/app.py"],
    )

    assert manifest == {
        "schema": 1,
        "commit": "49d8196e990bb08cb25c35feef9bb2f3c8a92910",
        "files": {"saturn/app.py": _sha256(source)},
    }


def test_verify_manifest_detects_deployed_source_drift(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("reviewed\n")
    payload = create_manifest(tmp_path, commit="a" * 40, paths=["app.py"])
    manifest_path = tmp_path / ".saturn-deployment.json"
    manifest_path.write_text(json.dumps(payload))
    source.write_text("changed after review\n")

    assert verify_manifest(tmp_path, manifest_path) == ["hash mismatch: app.py"]


def test_manifest_rejects_paths_outside_deployment_root(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope")

    with pytest.raises(ValueError, match="unsafe deployment path"):
        create_manifest(tmp_path, commit="b" * 40, paths=["../outside.txt"])


def test_verify_manifest_rejects_extra_sitecustomize(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("reviewed\n")
    payload = create_manifest(tmp_path, commit="a" * 40, paths=["app.py"])
    manifest_path = tmp_path / ".saturn-deployment.json"
    manifest_path.write_text(json.dumps(payload))
    (tmp_path / "sitecustomize.py").write_text("import malware\n")

    assert verify_manifest(tmp_path, manifest_path) == ["unexpected file: sitecustomize.py"]


def test_verify_manifest_rejects_extra_symlink(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("reviewed\n")
    payload = create_manifest(tmp_path, commit="a" * 40, paths=["app.py"])
    manifest_path = tmp_path / ".saturn-deployment.json"
    manifest_path.write_text(json.dumps(payload))
    (tmp_path / "alias.py").symlink_to(source)

    assert verify_manifest(tmp_path, manifest_path) == ["unexpected symlink: alias.py"]


def test_activate_release_atomically_switches_and_can_roll_back(tmp_path):
    commit_a = "a" * 40
    commit_b = "b" * 40
    for commit in (commit_a, commit_b):
        release = tmp_path / "releases" / commit
        release.mkdir(parents=True)
        (release / "app.py").write_text(f"{commit}\n")
        payload = create_manifest(release, commit=commit, paths=["app.py"])
        (release / ".saturn-deployment.json").write_text(json.dumps(payload))
        python = tmp_path / "venvs" / commit / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n")

    activate_release(tmp_path, commit_a)
    assert (tmp_path / "current").resolve() == (tmp_path / "releases" / commit_a)
    activate_release(tmp_path, commit_b)
    assert (tmp_path / "current").resolve() == (tmp_path / "releases" / commit_b)
    activate_release(tmp_path, commit_a)
    assert (tmp_path / "current").resolve() == (tmp_path / "releases" / commit_a)


def test_failed_release_verification_leaves_current_unchanged(tmp_path):
    commit_a = "a" * 40
    commit_b = "b" * 40
    for commit in (commit_a, commit_b):
        release = tmp_path / "releases" / commit
        release.mkdir(parents=True)
        (release / "app.py").write_text(f"{commit}\n")
        payload = create_manifest(release, commit=commit, paths=["app.py"])
        (release / ".saturn-deployment.json").write_text(json.dumps(payload))
        python = tmp_path / "venvs" / commit / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n")
    activate_release(tmp_path, commit_a)
    (tmp_path / "releases" / commit_b / "sitecustomize.py").write_text("import malware\n")

    with pytest.raises(ValueError, match="release verification failed"):
        activate_release(tmp_path, commit_b)

    assert (tmp_path / "current").resolve() == (tmp_path / "releases" / commit_a)


def test_activate_release_rejects_symlinked_environment_python(tmp_path):
    commit = "a" * 40
    release = tmp_path / "releases" / commit
    release.mkdir(parents=True)
    (release / "app.py").write_text("reviewed\n")
    payload = create_manifest(release, commit=commit, paths=["app.py"])
    (release / ".saturn-deployment.json").write_text(json.dumps(payload))
    python = tmp_path / "venvs" / commit / "bin" / "python"
    python.parent.mkdir(parents=True)
    interpreter = tmp_path / "python3"
    interpreter.write_text("#!/bin/sh\n")
    python.symlink_to(interpreter)

    with pytest.raises(ValueError, match="release environment is missing"):
        activate_release(tmp_path, commit)


def test_materialized_python_launcher_quotes_target_and_arguments(tmp_path):
    target = tmp_path / "runtime with spaces;and-metacharacters"
    target.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n")
    target.chmod(0o755)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(target)
    script = Path(__file__).parents[1] / "scripts" / "materialize-venv-python.sh"

    subprocess.run([script, python], check=True)
    result = subprocess.run(
        [python, "argument with spaces", ";$(not-a-command)"],
        check=True, capture_output=True, text=True,
    )

    assert not python.is_symlink()
    assert result.stdout.splitlines() == ["argument with spaces", ";$(not-a-command)"]


def test_materialized_python_launcher_rejects_target_drift(tmp_path):
    target = tmp_path / "runtime"
    target.write_text("#!/usr/bin/env bash\nexit 0\n")
    target.chmod(0o755)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(target)
    script = Path(__file__).parents[1] / "scripts" / "materialize-venv-python.sh"
    subprocess.run([script, python], check=True)
    target.write_text("#!/usr/bin/env bash\nexit 42\n")

    result = subprocess.run([python], capture_output=True, text=True)

    assert result.returncode == 126
    assert "interpreter changed after deployment" in result.stderr


def test_record_git_deployment_requires_exact_commit_contents(tmp_path):
    repo = tmp_path / "repo"
    deployed = tmp_path / "deployed"
    repo.mkdir()
    deployed.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Luke Steuber"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "luke@example.test"], check=True)
    (repo / "app.py").write_text("reviewed\n")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "reviewed"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    (deployed / "app.py").write_text("drifted\n")

    with pytest.raises(ValueError, match="differs from commit"):
        record_git_deployment(repo, deployed, commit)


def test_record_git_deployment_rejects_reused_stage_with_sitecustomize(tmp_path):
    repo = tmp_path / "repo"
    deployed = tmp_path / "deployed"
    repo.mkdir()
    deployed.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Luke Steuber"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "luke@example.test"], check=True)
    (repo / "app.py").write_text("reviewed\n")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "reviewed"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    (deployed / "app.py").write_text("reviewed\n")
    (deployed / "sitecustomize.py").write_text("import malware\n")

    with pytest.raises(ValueError, match="unexpected file: sitecustomize.py"):
        record_git_deployment(repo, deployed, commit)
