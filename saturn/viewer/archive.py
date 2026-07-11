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


def resolve_archive_artifact(
    directory: Path | None, artifact_id: str
) -> ArchiveArtifact | None:
    """Resolve a complete, canonical artifact pair inside the archive root."""
    if directory is None:
        return None
    if _ARCHIVE_ID.fullmatch(artifact_id) is None:
        return None
    base = Path(directory).resolve()
    if not base.is_dir():
        return None
    resolved: dict[str, Path] = {}
    for suffix in (".html", ".ipynb"):
        candidate = (base / f"{artifact_id}{suffix}").resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        resolved[suffix] = candidate
    return ArchiveArtifact(
        artifact_id,
        resolved[".html"],
        resolved[".ipynb"],
    )


def list_archive(directory: Path | None) -> list[ArchiveArtifact]:
    """List complete HTML/notebook pairs, excluding hidden or unsafe names."""
    if directory is None:
        return []
    base = Path(directory).resolve()
    if not base.is_dir():
        return []
    artifacts: list[ArchiveArtifact] = []
    for html_path in sorted(base.glob("*.html")):
        artifact_id = html_path.stem
        artifact = resolve_archive_artifact(base, artifact_id)
        if artifact is not None:
            artifacts.append(artifact)
    return artifacts
