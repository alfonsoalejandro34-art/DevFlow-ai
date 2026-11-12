"""Unsandboxed local runner.

Runs pytest directly on the host with a scrubbed environment, and its own process group
(killed as a whole on timeout or cancel). It provides **no isolation** and is only constructed when
``DEVFLOW_RUNNER=local`` and ``DEVFLOW_ALLOW_UNSANDBOXED_RUNNER=true`` are both set in the
environment. Runs it executes are recorded with ``sandboxed=false`` and labelled in the UI.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time

from devflow.errors import CommandFailed
from devflow.runners.base import (
    Availability,
    RunOutcome,
    RunSpec,
    pytest_args,
    pytest_config_name,
    supervise,
)
from devflow.security.commands import minimal_env, start_process


class LocalRunner:
    name = "local"
    sandboxed = False

    def availability(self) -> Availability:
        if importlib.util.find_spec("pytest_timeout") is None:
            return Availability(False, "pytest-timeout is not installed in the DevFlow environment")
        return Availability(
            True, "UNSANDBOXED: tests run directly on the host", sys.version.split()[0]
        )

    def run(self, spec: RunSpec, cancel: threading.Event) -> RunOutcome:
        env = minimal_env(
            {
                "HOME": str(spec.output_dir),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        name, in_source = pytest_config_name(spec.source_dir, spec.output_dir)
        config = str((spec.source_dir if in_source else spec.output_dir) / name)
        started = time.monotonic()
        with spec.stdout_path.open("wb") as out, spec.stderr_path.open("wb") as err:
            try:
                proc = start_process(
                    "python",
                    pytest_args(
                        str(spec.junit_path), spec.timeout_seconds, str(spec.source_dir), config
                    ),
                    configured=sys.executable,
                    cwd=spec.source_dir,
                    env=env,
                    stdout=out,
                    stderr=err,
                    new_session=True,
                )
            except CommandFailed as exc:
                return RunOutcome(None, 0, infra_error=exc.message)
            return supervise(proc, spec, cancel, lambda: _kill_group(proc), started)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
