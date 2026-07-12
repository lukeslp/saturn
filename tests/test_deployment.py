from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from saturn.deployment import create_manifest, record_git_deployment, verify_manifest


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
