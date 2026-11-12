"""Throwaway repository copies for agent-mode analysis, and the diff of what changed.

The agent edits a copy of the run's source snapshot. DevFlow then compares the copy
with the snapshot in Python (``difflib``) rather than running git inside a directory
the agent controlled, so nothing the agent wrote can configure or trigger a program.
"""

from __future__ import annotations

import difflib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Files that steer AI coding agents. A hostile repository could use them to inject
# instructions or (for some tools) configure hooks, so they never reach the agent.
AGENT_CONTROL_FILES = frozenset(
    {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md", ".mcp.json", ".cursorrules"}
)
AGENT_CONTROL_DIRS = frozenset({".claude", ".codex", ".cursor", ".gemini"})
_IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
MAX_CHANGED_FILE_BYTES = 1_000_000


@dataclass
class WorkspaceDiff:
    diff: str
    changed_files: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def is_agent_control_path(rel: str) -> bool:
    parts = rel.split("/")
    return parts[-1] in AGENT_CONTROL_FILES or any(p in AGENT_CONTROL_DIRS for p in parts)


def prepare_workspace(snapshot: Path, dest: Path) -> list[str]:
    """Copy ``snapshot`` to ``dest`` without agent-control files. Returns what was left out."""
    excluded: list[str] = []

    def ignore(directory: str, names: list[str]) -> set[str]:
        rel_dir = Path(directory).relative_to(snapshot).as_posix()
        skipped = set()
        for name in names:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if name in _IGNORED_DIRS:
                skipped.add(name)
            elif is_agent_control_path(rel):
                skipped.add(name)
                excluded.append(rel)
        return skipped

    shutil.copytree(snapshot, dest, ignore=ignore, symlinks=True)
    return sorted(excluded)


def _files(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        for name in filenames + [d for d in dirnames if (Path(directory) / d).is_symlink()]:
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if not is_agent_control_path(rel):
                found[rel] = path
    return found


def _lines(data: bytes) -> list[str]:
    text = data.decode("utf-8")
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def diff_workspace(original: Path, edited: Path) -> WorkspaceDiff:
    """Unified diff (git-apply compatible, a/ b/ prefixes) of ``edited`` against ``original``."""
    before, after = _files(original), _files(edited)
    result = WorkspaceDiff(diff="")
    chunks: list[str] = []
    for rel in sorted(set(before) | set(after)):
        old, new = before.get(rel), after.get(rel)
        if new is not None and new.is_symlink():
            result.problems.append(f"{rel}: agent created a symbolic link")
            continue
        old_bytes = old.read_bytes() if old else b""
        new_bytes = new.read_bytes() if new else b""
        if old is not None and new is not None and old_bytes == new_bytes:
            continue
        if len(new_bytes) > MAX_CHANGED_FILE_BYTES:
            result.problems.append(
                f"{rel}: changed file is larger than {MAX_CHANGED_FILE_BYTES} bytes"
            )
            continue
        try:
            old_lines, new_lines = _lines(old_bytes), _lines(new_bytes)
        except UnicodeDecodeError:
            result.problems.append(f"{rel}: binary or non-UTF-8 content")
            continue
        header = [f"diff --git a/{rel} b/{rel}\n"]
        if old is None:
            header.append("new file mode 100644\n")
        elif new is None:
            header.append("deleted file mode 100644\n")
        body = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel}" if old else "/dev/null",
                tofile=f"b/{rel}" if new else "/dev/null",
                n=3,
            )
        )
        if body:
            chunks.append("".join(header + body))
            result.changed_files.append(rel)
    result.diff = "".join(chunks)
    return result
