"""Read-only Git access hardened against hostile repository configuration.

A repository's own ``.git/config`` can define hooks, ``core.fsmonitor`` commands, or
filter drivers that execute arbitrary programs. DevFlow therefore:

* ignores system and global git config and disables hooks and fsmonitor,
* never runs porcelain commands that consult the working tree (``status``, ``checkout``),
* builds snapshots from raw blobs via ``ls-tree`` + ``cat-file`` so no filters run.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from devflow.errors import CommandFailed, SecurityViolation, ValidationFailed
from devflow.security.commands import minimal_env, run_command
from devflow.security.paths import resolve_within, safe_relative_path

_REV_PATTERN = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/@^~\-]{1,200}$")
_SAFE_CONFIG = (
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.allow=never",
    "-c", "core.sshCommand=false",
    "-c", "credential.helper=",
    "--no-replace-objects",
)  # fmt: skip
_FIELD_SEP = "\x1f"

MAX_SNAPSHOT_FILES = 5000
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    committed_at: datetime
    message: str


@dataclass
class SnapshotInfo:
    files: int = 0
    bytes: int = 0
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    kind: str
    sha: str
    path: str


def _git_env() -> dict[str, str]:
    return minimal_env(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "HOME": tempfile.gettempdir(),
        }
    )


def git(repo: Path, *args: str, input_bytes: bytes | None = None, timeout: float = 30) -> bytes:
    result = run_command(
        "git",
        [*_SAFE_CONFIG, "-C", str(repo), *args],
        env=_git_env(),
        input_bytes=input_bytes,
        timeout=timeout,
    )
    return result.stdout


def validate_rev(rev: str) -> str:
    if not _REV_PATTERN.match(rev):
        raise ValidationFailed(f"Invalid revision: {rev[:80]!r}")
    return rev


def resolve_commit(repo: Path, rev: str) -> str:
    validate_rev(rev)
    try:
        out = git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
    except CommandFailed as exc:
        raise ValidationFailed(f"Unknown revision: {rev}") from exc
    return out.decode().strip()


def current_branch(repo: Path) -> str | None:
    try:
        out = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    except CommandFailed:
        return None  # detached HEAD
    return out.decode().strip() or None


def head_commit(repo: Path) -> str | None:
    try:
        return resolve_commit(repo, "HEAD")
    except ValidationFailed:
        return None  # repository without commits


def list_branches(repo: Path) -> list[str]:
    out = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [line for line in out.decode().splitlines() if line]


def recent_commits(repo: Path, rev: str = "HEAD", limit: int = 20) -> list[Commit]:
    sha = resolve_commit(repo, rev)
    fmt = _FIELD_SEP.join(["%H", "%an", "%cI", "%s"])
    out = git(repo, "log", f"--format={fmt}", f"--max-count={max(1, min(limit, 200))}", sha, "--")
    commits = []
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.split(_FIELD_SEP)
        if len(parts) == 4:
            commits.append(
                Commit(parts[0], parts[1], datetime.fromisoformat(parts[2]), parts[3][:500])
            )
    return commits


def commit_info(repo: Path, sha: str) -> Commit:
    commits = recent_commits(repo, sha, limit=1)
    if not commits:
        raise ValidationFailed(f"Unknown revision: {sha}")
    return commits[0]


def list_tree(repo: Path, sha: str) -> list[TreeEntry]:
    out = git(repo, "ls-tree", "-r", "-z", "--full-tree", sha, timeout=60)
    entries = []
    for record in out.split(b"\x00"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, kind, obj = meta.decode().split(" ")
        entries.append(TreeEntry(mode, kind, obj, path.decode("utf-8", "replace")))
    return entries


def snapshot(repo: Path, sha: str, dest: Path) -> SnapshotInfo:
    """Materialize the tree of ``sha`` into ``dest`` without touching the repository's
    working tree and without running any git filters or hooks."""
    info = SnapshotInfo()
    blobs: list[TreeEntry] = []
    for entry in list_tree(repo, sha):
        if entry.kind != "blob" or entry.mode not in ("100644", "100755"):
            info.skipped.append(f"{entry.path} (mode {entry.mode})")
            continue
        try:
            safe_relative_path(entry.path)
        except SecurityViolation:
            info.skipped.append(f"{entry.path} (unsafe path)")
            continue
        blobs.append(entry)
    if len(blobs) > MAX_SNAPSHOT_FILES:
        raise ValidationFailed(f"Repository has more than {MAX_SNAPSHOT_FILES} files")

    dest.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{b.sha}\n" for b in blobs).encode()
    out = git(repo, "cat-file", "--batch", input_bytes=payload, timeout=120)
    offset = 0
    for entry in blobs:
        header_end = out.index(b"\n", offset)
        _obj, _kind, size_text = out[offset:header_end].decode().split(" ")
        size = int(size_text)
        start = header_end + 1
        offset = start + size + 1  # trailing newline after each object
        if size > MAX_FILE_BYTES:
            info.skipped.append(f"{entry.path} (larger than {MAX_FILE_BYTES} bytes)")
            continue
        info.bytes += size
        if info.bytes > MAX_SNAPSHOT_BYTES:
            raise ValidationFailed(f"Repository snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes")
        target = resolve_within(dest, entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(out[start : start + size])
        target.chmod(0o755 if entry.mode == "100755" else 0o644)
        info.files += 1
    return info


def create_branch_from_files(
    repo: Path,
    base_sha: str,
    changes: dict[str, bytes | None],
    branch: str,
    message: str,
) -> str:
    """Commit ``changes`` on top of ``base_sha`` as a new branch using plumbing only.

    The user's working tree, index, and current branch are never touched, and the
    branch must not already exist. ``None`` content deletes the path.
    """
    validate_rev(branch)
    ref = f"refs/heads/{branch}"
    exists = run_command(
        "git",
        [*_SAFE_CONFIG, "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
        env=_git_env(),
        check=False,
    )
    if exists.returncode == 0:
        raise ValidationFailed(f"Branch {branch!r} already exists")
    with tempfile.TemporaryDirectory(prefix="devflow-index-") as tmp:
        env = _git_env() | {
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_AUTHOR_NAME": "DevFlow AI",
            "GIT_AUTHOR_EMAIL": "devflow@localhost",
            "GIT_COMMITTER_NAME": "DevFlow AI",
            "GIT_COMMITTER_EMAIL": "devflow@localhost",
        }

        def plumbing(*args: str, data: bytes | None = None) -> str:
            result = run_command(
                "git", [*_SAFE_CONFIG, "-C", str(repo), *args], env=env, input_bytes=data
            )
            return result.stdout.decode().strip()

        plumbing("read-tree", base_sha)
        for path, content in changes.items():
            safe_relative_path(path)
            if content is None:
                plumbing("update-index", "--force-remove", "--", path)
                continue
            blob = plumbing("hash-object", "-w", "--stdin", data=content)
            plumbing("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
        tree = plumbing("write-tree")
        commit = plumbing("commit-tree", tree, "-p", base_sha, data=message.encode())
        plumbing("update-ref", ref, commit, "0" * 40)
    return commit
