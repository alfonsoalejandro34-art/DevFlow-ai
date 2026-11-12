"""The single choke point for launching host processes.

Only executables named in ``ALLOWED_EXECUTABLES`` may run, arguments are always passed
as a list (never through a shell), and the environment is rebuilt from scratch so host
credentials are not inherited.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from devflow.errors import CommandFailed, SecurityViolation

# Logical name -> purpose. The concrete binary path comes from settings.
ALLOWED_EXECUTABLES = frozenset({"git", "docker", "python", "claude", "codex"})

_BASE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR")


def minimal_env(
    extra: Mapping[str, str] | None = None, *, keep_home: bool = False
) -> dict[str, str]:
    env = {k: os.environ[k] for k in _BASE_ENV_KEYS if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    if keep_home:
        env["HOME"] = str(Path.home())
    if extra:
        env.update(extra)
    return env


def resolve_executable(logical: str, configured: str | None = None) -> str:
    if logical not in ALLOWED_EXECUTABLES:
        raise SecurityViolation(f"Executable {logical!r} is not on the allowlist")
    binary = configured or logical
    found = shutil.which(binary)
    if found is None:
        raise CommandFailed(f"{binary!r} was not found on PATH")
    return found


def run_command(
    logical: str,
    args: Sequence[str],
    *,
    configured: str | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    timeout: float = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run an allowlisted executable with list arguments and a scrubbed environment."""
    if any("\x00" in a for a in args):
        raise SecurityViolation("NUL byte in command argument")
    executable = resolve_executable(logical, configured)
    try:
        result = subprocess.run(
            [executable, *args],
            cwd=cwd,
            env=dict(env) if env is not None else minimal_env(),
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandFailed(f"{logical} timed out after {timeout:.0f}s") from exc
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:500]
        raise CommandFailed(f"{logical} exited with {result.returncode}: {detail}")
    return result


def start_process(
    logical: str,
    args: Sequence[str],
    *,
    configured: str | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: IO[bytes],
    stderr: IO[bytes],
    new_session: bool = False,
) -> subprocess.Popen[bytes]:
    """Start a long-running allowlisted process whose output streams to files."""
    if any("\x00" in a for a in args):
        raise SecurityViolation("NUL byte in command argument")
    executable = resolve_executable(logical, configured)
    return subprocess.Popen(
        [executable, *args],
        cwd=cwd,
        env=dict(env) if env is not None else minimal_env(),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        shell=False,
        start_new_session=new_session,
    )
