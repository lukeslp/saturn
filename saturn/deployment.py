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
import uuid
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


def _deployed_entries(root: Path) -> tuple[set[str], set[str]]:
    """Return regular files and symlinks without following deployed links."""
    regular: set[str] = set()
    symlinks: set[str] = set()
    root = root.resolve()
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in names:
            path = directory_path / name
            if path.is_symlink():
                symlinks.add(path.relative_to(root).as_posix())
        for name in files:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                symlinks.add(relative)
            elif path.is_file():
                regular.add(relative)
    return regular, symlinks


def _inventory_errors(root: Path, expected: set[str], *, manifest_allowed: bool) -> list[str]:
    regular, symlinks = _deployed_entries(root)
    allowed = set(expected)
    if manifest_allowed:
        allowed.add(MANIFEST_NAME)
    errors = [f"unexpected file: {path}" for path in sorted(regular - allowed)]
    errors.extend(f"unexpected symlink: {path}" for path in sorted(symlinks))
    return errors


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
    errors.extend(_inventory_errors(root, set(files), manifest_allowed=True))
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
    if (root / MANIFEST_NAME).exists() or (root / MANIFEST_NAME).is_symlink():
        raise ValueError("deployment staging directory is not fresh: manifest already exists")
    inventory_errors = _inventory_errors(root, set(names), manifest_allowed=False)
    if inventory_errors:
        raise ValueError("deployment staging differs from commit: " + "; ".join(inventory_errors))
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


def activate_release(deploy_root: Path, commit: str) -> Path:
    """Atomically point ``current`` at one verified immutable release."""
    if not _COMMIT.fullmatch(commit) or len(commit) != 40:
        raise ValueError("release activation requires a full 40-character commit SHA")
    deploy_root = deploy_root.resolve()
    release = deploy_root / "releases" / commit
    manifest = release / MANIFEST_NAME
    errors = verify_manifest(release, manifest)
    if errors:
        raise ValueError("release verification failed: " + "; ".join(errors))
    if deployment_commit(manifest) != commit:
        raise ValueError("release manifest commit does not match release directory")
    python = deploy_root / "venvs" / commit / "bin" / "python"
    if not python.is_file() or python.is_symlink():
        raise ValueError(f"release environment is missing: {python}")

    current = deploy_root / "current"
    temporary = deploy_root / f".current-{uuid.uuid4().hex}"
    temporary.symlink_to(Path("releases") / commit, target_is_directory=True)
    try:
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description="Record or verify a Saturn deployment")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("commit")
    record.add_argument("root", type=Path)
    record.add_argument("--repo", type=Path, default=Path.cwd())
    verify = sub.add_parser("verify")
    verify.add_argument("root", type=Path)
    activate = sub.add_parser("activate")
    activate.add_argument("commit")
    activate.add_argument("deploy_root", type=Path)
    args = parser.parse_args()
    if args.command == "record":
        print(record_git_deployment(args.repo, args.root, args.commit))
        return 0
    if args.command == "activate":
        print(activate_release(args.deploy_root, args.commit))
        return 0
    errors = verify_manifest(args.root)
    if errors:
        print("\n".join(errors))
        return 1
    print("deployment matches manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
