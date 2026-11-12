"""Runner interface. A runner executes pytest against a prepared source snapshot and
reports raw outcomes; parsing and persistence happen in the job service."""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MAX_LOG_BYTES = 10 * 1024 * 1024
_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class RunSpec:
    run_id: int
    source_dir: Path
    output_dir: Path
    timeout_seconds: int
    memory_mb: int
    cpus: float
    pids_limit: int

    @property
    def junit_path(self) -> Path:
        return self.output_dir / "junit.xml"

    @property
    def stdout_path(self) -> Path:
        return self.output_dir / "stdout.log"

    @property
    def stderr_path(self) -> Path:
        return self.output_dir / "stderr.log"


@dataclass
class RunOutcome:
    exit_code: int | None
    wall_ms: int
    timed_out: bool = False
    cancelled: bool = False
    output_limit_exceeded: bool = False
    infra_error: str | None = None
    details: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str | None = None
    version: str | None = None


class Runner(Protocol):
    name: str
    sandboxed: bool

    def availability(self) -> Availability: ...

    def run(self, spec: RunSpec, cancel: threading.Event) -> RunOutcome: ...


_CONFIG_MARKERS = (
    ("pytest.ini", None),
    (".pytest.ini", None),
    ("pyproject.toml", "[tool.pytest"),
    ("tox.ini", "[pytest]"),
    ("setup.cfg", "[tool:pytest]"),
)


def pytest_config_name(source_dir: Path, output_dir: Path) -> tuple[str, bool]:
    """Pick the pytest config file for a run.

    Returns ``(name, in_source)``: the repository's own config file at its root, or an
    empty ``pytest.ini`` written to the output directory. Passing it explicitly with
    ``-c`` stops pytest from searching parent directories, where it could pick up a
    configuration that does not belong to the repository.
    """
    for name, section in _CONFIG_MARKERS:
        candidate = source_dir / name
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and (section is None or section in candidate.read_text(errors="replace"))
        ):
            return name, True
    (output_dir / "pytest.ini").write_text("[pytest]\n")
    return "pytest.ini", False


def pytest_args(junit_path: str, timeout_seconds: int, rootdir: str, config_path: str) -> list[str]:
    # xunit1 includes file/line attributes on each testcase.
    per_test_timeout = max(5, int(timeout_seconds * 0.8))
    return [
        "-m", "pytest",
        "-c", config_path,
        f"--rootdir={rootdir}",
        "-p", "no:cacheprovider",
        "-o", "junit_family=xunit1",
        f"--junitxml={junit_path}",
        f"--timeout={per_test_timeout}",
        "--color=no",
        "-q",
        "-rfE",
    ]  # fmt: skip


def supervise(
    proc: subprocess.Popen[bytes],
    spec: RunSpec,
    cancel: threading.Event,
    kill: Callable[[], None],
    started: float,
) -> RunOutcome:
    """Wait for ``proc`` while enforcing the wall-clock timeout, cancellation, and the
    log size cap. ``kill`` must terminate the workload (container or process group)."""
    timed_out = cancelled = too_much_output = False
    while proc.poll() is None:
        elapsed = time.monotonic() - started
        if cancel.is_set():
            cancelled = True
        elif elapsed > spec.timeout_seconds:
            timed_out = True
        elif _log_bytes(spec) > MAX_LOG_BYTES:
            too_much_output = True
        if cancelled or timed_out or too_much_output:
            kill()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            break
        time.sleep(_POLL_SECONDS)
    return RunOutcome(
        exit_code=proc.returncode,
        wall_ms=int((time.monotonic() - started) * 1000),
        timed_out=timed_out,
        cancelled=cancelled,
        output_limit_exceeded=too_much_output,
    )


def _log_bytes(spec: RunSpec) -> int:
    total = 0
    for path in (spec.stdout_path, spec.stderr_path):
        with contextlib.suppress(FileNotFoundError):
            total += path.stat().st_size
    return total
