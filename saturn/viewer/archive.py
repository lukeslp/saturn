"""Allowlisted access to preserved pre-contract viewer artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_ARCHIVE_ID = re.compile(r"(?!\.{1,2}$)[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ArchiveArtifact:
    id: str
    html_path: Path
    notebook_path: Path


def archive_path(directory: Path | None, artifact_id: str, suffix: str) -> Path | None:
    """Resolve an allowlisted, top-level archive artifact or return ``None``."""
    if directory is None or suffix not in {".html", ".ipynb"}:
        return None
    if _ARCHIVE_ID.fullmatch(artifact_id) is None:
        return None
    base = Path(directory).resolve()
    candidate = (base / f"{artifact_id}{suffix}").resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def list_archive(directory: Path | None) -> list[ArchiveArtifact]:
    """List complete HTML/notebook pairs, excluding hidden or unsafe names."""
    if directory is None:
        return []
    base = Path(directory)
    if not base.is_dir():
        return []
    artifacts: list[ArchiveArtifact] = []
    for html_path in sorted(base.glob("*.html")):
        artifact_id = html_path.stem
        if _ARCHIVE_ID.fullmatch(artifact_id) is None:
            continue
        notebook_path = base / f"{artifact_id}.ipynb"
        if not notebook_path.is_file():
            continue
        artifacts.append(ArchiveArtifact(artifact_id, html_path, notebook_path))
    return artifacts
