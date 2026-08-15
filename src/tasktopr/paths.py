"""Repository-relative path canonicalization used by policy and typed models."""

from __future__ import annotations

import posixpath
from pathlib import Path


class PathSecurityError(ValueError):
    """Raised when a path is empty, absolute, or escapes the repository root."""


def normalize_relpath(relative_path: str) -> str:
    """Convert backslashes to forward slashes for consistent matching."""

    return relative_path.replace("\\", "/")


def canonicalize_repo_relpath(relative_path: str) -> str:
    """Collapse ``.`` / ``..`` and reject paths that leave the repository.

    Policy matching must run on this form. A raw
    ``src/../.github/workflows/ci.yml`` or ``./.github/workflows/ci.yml``
    does not match ``.github/workflows/**`` until parent and current-dir
    segments are removed.
    """

    if not relative_path or not relative_path.strip():
        raise PathSecurityError("Only non-empty repository-relative paths are allowed.")
    raw = normalize_relpath(relative_path)
    if Path(relative_path).is_absolute() or Path(raw).is_absolute():
        raise PathSecurityError("Only non-empty repository-relative paths are allowed.")
    if raw.startswith("/") or raw.startswith("//"):
        raise PathSecurityError("Only non-empty repository-relative paths are allowed.")
    if len(raw) >= 2 and raw[1] == ":":
        raise PathSecurityError("Only non-empty repository-relative paths are allowed.")
    collapsed = posixpath.normpath(raw)
    if collapsed in {".", ""} or collapsed.startswith("../"):
        raise PathSecurityError(f"Path escapes the repository root: {relative_path}")
    return collapsed


def resolved_repo_relpath(repo_root: Path, relative_path: str) -> str:
    """Return the on-disk repository-relative path that a write would hit.

    Canonicalization collapses ``.`` / ``..``. ``Path.resolve`` then expands
    Windows 8.3 names, junctions, and in-repo symlinks. Policy must run on
    this form or a short name / symlink can miss ``.github/workflows/**``.
    """

    collapsed = canonicalize_repo_relpath(relative_path)
    root = repo_root.resolve()
    candidate = (root / collapsed).resolve()
    if candidate == root or root not in candidate.parents:
        raise PathSecurityError(f"Path escapes the repository root: {relative_path}")
    return candidate.relative_to(root).as_posix()
