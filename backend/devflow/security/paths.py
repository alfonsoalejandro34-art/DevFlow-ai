"""Filesystem path validation for repository registration and for paths inside snapshots."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from devflow.errors import SecurityViolation, ValidationFailed

# Directories that may never be registered as, or contain, an allowed root.
_SENSITIVE_HOME_ENTRIES = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config",
    ".kube",
    ".docker",
    ".claude",
    ".codex",
    "Library",
)
_MAX_RELATIVE_PATH = 400


def sensitive_dirs() -> list[Path]:
    home = Path.home().resolve()
    return [home / name for name in _SENSITIVE_HOME_ENTRIES]


def validate_allowed_root(raw: str | Path) -> Path:
    """Validate a directory proposed as an allowed root for repository registration."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValidationFailed("Allowed directories must be absolute paths")
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise ValidationFailed(f"Directory does not exist: {resolved}")
    home = Path.home().resolve()
    if resolved == Path(resolved.anchor) or resolved == home:
        raise SecurityViolation("The filesystem root or home directory cannot be an allowed root")
    for sensitive in sensitive_dirs():
        if resolved == sensitive or resolved.is_relative_to(sensitive):
            raise SecurityViolation(f"{resolved} is inside a sensitive directory")
        if sensitive.is_relative_to(resolved):
            raise SecurityViolation(f"{resolved} would expose sensitive directory {sensitive}")
    return resolved


def validate_repository_path(raw: str, allowed_roots: Iterable[Path]) -> Path:
    """Resolve ``raw`` and ensure it is a non-bare Git repository inside an allowed root.

    Symlinks are resolved before the containment check, so a link inside an allowed
    root that points elsewhere is rejected.
    """
    if not raw or "\x00" in raw or len(raw) > 1024:
        raise ValidationFailed("Invalid repository path")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValidationFailed("Repository path must be absolute")
    resolved = candidate.resolve()
    roots = [Path(r).resolve() for r in allowed_roots]
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise SecurityViolation(
            "Repository path is outside the approved directories. "
            "Add its parent directory under Settings > Repository directories first."
        )
    for sensitive in sensitive_dirs():
        if resolved.is_relative_to(sensitive):
            raise SecurityViolation("Repository path is inside a sensitive directory")
    if not resolved.is_dir():
        raise ValidationFailed(f"Directory does not exist: {resolved}")
    git_dir = resolved / ".git"
    # A `.git` *file* (linked worktree or submodule) can point anywhere on disk.
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise ValidationFailed("Path is not the top level of a non-bare Git repository")
    return resolved


def safe_relative_path(raw: str) -> PurePosixPath:
    """Validate a repository-relative path (from git, a traceback, or a patch)."""
    if not raw or "\x00" in raw or "\\" in raw or "//" in raw or len(raw) > _MAX_RELATIVE_PATH:
        raise SecurityViolation(f"Unsafe path: {raw[:80]!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("..", "") for part in path.parts):
        raise SecurityViolation(f"Unsafe path: {raw[:80]!r}")
    if any(part == ".git" for part in path.parts):
        raise SecurityViolation(f"Path touches git metadata: {raw[:80]!r}")
    return path


def resolve_within(base: Path, relative: str) -> Path:
    """Join a relative path to ``base`` and verify the result stays inside it."""
    rel = safe_relative_path(relative)
    target = (base / rel).resolve()
    if not target.is_relative_to(base.resolve()):
        raise SecurityViolation(f"Path escapes its base directory: {relative[:80]!r}")
    return target


def make_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path
