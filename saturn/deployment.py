"""Deployment provenance recording and verification.

The manifest is generated after extracting one reviewed Git commit. It records
that commit plus SHA-256 hashes for every tracked file in the deployed tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

MANIFEST_NAME = ".saturn-deployment.json"
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"unsafe deployment path: {relative}") from exc
    if Path(relative).is_absolute() or relative in {"", "."}:
        raise ValueError(f"unsafe deployment path: {relative}")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(root: Path, *, commit: str, paths: Iterable[str]) -> dict:
    if not _COMMIT.fullmatch(commit):
        raise ValueError("deployment commit must be a 7-40 character lowercase SHA")
    files: dict[str, str] = {}
    for relative in sorted(set(paths)):
        path = _safe_path(root, relative)
        if not path.is_file():
            raise ValueError(f"missing deployed file: {relative}")
        files[relative] = _sha256(path)
    return {"schema": 1, "commit": commit, "files": files}


def verify_manifest(root: Path, manifest_path: Path | None = None) -> list[str]:
    manifest_path = manifest_path or root / MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid deployment manifest: {exc}"]
    errors: list[str] = []
    if payload.get("schema") != 1 or not _COMMIT.fullmatch(str(payload.get("commit", ""))):
        errors.append("invalid deployment manifest metadata")
    files = payload.get("files")
    if not isinstance(files, dict):
        return errors + ["invalid deployment file inventory"]
    for relative, expected in sorted(files.items()):
        try:
            path = _safe_path(root, relative)
        except ValueError:
            errors.append(f"unsafe deployment path: {relative}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {relative}")
        elif _sha256(path) != expected:
            errors.append(f"hash mismatch: {relative}")
    return errors


def deployment_commit(manifest_path: Path | None = None) -> str | None:
    configured = os.environ.get("SATURN_DEPLOY_MANIFEST")
    path = manifest_path or Path(configured or MANIFEST_NAME)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    commit = str(payload.get("commit", ""))
    return commit if _COMMIT.fullmatch(commit) else None


def record_git_deployment(repo: Path, root: Path, commit: str) -> Path:
    resolved = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{commit}^{{commit}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    names = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", resolved],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    payload = create_manifest(root, commit=resolved, paths=names)
    for relative, deployed_hash in payload["files"].items():
        source = subprocess.run(
            ["git", "-C", str(repo), "show", f"{resolved}:{relative}"],
            check=True, capture_output=True,
        ).stdout
        if hashlib.sha256(source).hexdigest() != deployed_hash:
            raise ValueError(f"deployed file differs from commit: {relative}")
    destination = root / MANIFEST_NAME
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Record or verify a Saturn deployment")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("commit")
    record.add_argument("root", type=Path)
    record.add_argument("--repo", type=Path, default=Path.cwd())
    verify = sub.add_parser("verify")
    verify.add_argument("root", type=Path)
    args = parser.parse_args()
    if args.command == "record":
        print(record_git_deployment(args.repo, args.root, args.commit))
        return 0
    errors = verify_manifest(args.root)
    if errors:
        print("\n".join(errors))
        return 1
    print("deployment matches manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
