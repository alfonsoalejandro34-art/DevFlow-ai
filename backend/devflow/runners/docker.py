"""Docker sandbox runner.

Each run gets a fresh container with no network, a read-only root filesystem, all
Linux capabilities dropped, ``no-new-privileges``, a non-root UID, CPU/memory/PID
limits, and exactly two bind mounts: the source snapshot (read-only) and an empty
per-run output directory. ``validate_docker_command`` re-checks the final argument
list before every launch so a future code change cannot silently weaken it.
"""

from __future__ import annotations

import secrets
import threading
import time

from devflow.errors import CommandFailed, SecurityViolation
from devflow.runners.base import Availability, RunOutcome, RunSpec, pytest_args, supervise
from devflow.security.commands import minimal_env, run_command, start_process

SANDBOX_UID = "10001:10001"
_FORBIDDEN_FLAGS = frozenset(
    {
        "--privileged",
        "--cap-add",
        "--pid",
        "--ipc",
        "--uts",
        "--userns",
        "--device",
        "--volumes-from",
        "-v",
        "--volume",
        "--net",
        "-p",
        "--publish",
        "-P",
        "--publish-all",
    }
)


class DockerRunner:
    name = "docker"
    sandboxed = True

    def __init__(self, image: str, binary: str = "docker") -> None:
        self.image = image
        self.binary = binary

    def _docker(self, *args: str, timeout: float = 30, check: bool = True) -> str:
        result = run_command(
            "docker",
            list(args),
            configured=self.binary,
            env=_docker_env(),
            timeout=timeout,
            check=check,
        )
        return result.stdout.decode().strip()

    def availability(self) -> Availability:
        try:
            version = self._docker("version", "--format", "{{.Server.Version}}", timeout=10)
        except CommandFailed as exc:
            return Availability(False, f"Docker engine unavailable: {exc.message}")
        try:
            self._docker("image", "inspect", "--format", "{{.Id}}", self.image, timeout=10)
        except CommandFailed:
            return Availability(
                False, f"Runner image {self.image!r} not built; run `make runner-image`", version
            )
        return Availability(True, None, version)

    def build_command(self, spec: RunSpec, container_name: str) -> list[str]:
        return [
            "run",
            "--rm",
            "--name", container_name,
            "--label", "devflow.managed=true",
            "--label", f"devflow.run_id={spec.run_id}",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",  # noqa: S108 - path inside the container
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", SANDBOX_UID,
            "--memory", f"{spec.memory_mb}m",
            "--memory-swap", f"{spec.memory_mb}m",
            "--cpus", f"{spec.cpus:g}",
            "--pids-limit", str(spec.pids_limit),
            "--ulimit", "nofile=1024:1024",
            "--env", "HOME=/tmp",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTHONUNBUFFERED=1",
            "--mount", f"type=bind,source={spec.source_dir},target=/src,readonly",
            "--mount", f"type=bind,source={spec.output_dir},target=/out",
            "--workdir", "/src",
            self.image,
            "python", *pytest_args("/out/junit.xml", spec.timeout_seconds),
        ]  # fmt: skip

    def run(self, spec: RunSpec, cancel: threading.Event) -> RunOutcome:
        container = f"devflow-run-{spec.run_id}-{secrets.token_hex(4)}"
        command = self.build_command(spec, container)
        validate_docker_command(command, spec, self.image)
        spec.output_dir.chmod(0o777)  # container UID differs from the host user
        started = time.monotonic()
        with spec.stdout_path.open("wb") as out, spec.stderr_path.open("wb") as err:
            try:
                proc = start_process(
                    "docker",
                    command,
                    configured=self.binary,
                    env=_docker_env(),
                    stdout=out,
                    stderr=err,
                )
            except CommandFailed as exc:
                return RunOutcome(None, 0, infra_error=exc.message)
            try:
                outcome = supervise(proc, spec, cancel, lambda: self._kill(container), started)
            finally:
                self._docker("rm", "--force", container, check=False)
        spec.output_dir.chmod(0o700)
        # 125-127 are docker CLI failures (daemon/image/command), not test results.
        if outcome.exit_code in (125, 126, 127) and not (outcome.cancelled or outcome.timed_out):
            stderr_tail = spec.stderr_path.read_text(errors="replace")[-500:]
            outcome.infra_error = (
                f"Docker failed to start the test container: {stderr_tail.strip()}"
            )
        return outcome

    def _kill(self, container: str) -> None:
        self._docker("kill", container, check=False, timeout=15)


def validate_docker_command(command: list[str], spec: RunSpec, image: str) -> None:
    """Check the ``docker run`` options (everything before the image name)."""
    if image not in command:
        raise SecurityViolation("Runner image missing from container command")
    command = command[: command.index(image)]
    joined = " ".join(command)
    for i, arg in enumerate(command):
        flag = arg.split("=", 1)[0]
        if flag in _FORBIDDEN_FLAGS:
            raise SecurityViolation(f"Refusing to start container with {flag}")
        value = (
            arg.split("=", 1)[1]
            if "=" in arg and arg.startswith("--")
            else (command[i + 1] if i + 1 < len(command) else "")
        )
        if flag == "--network" and value != "none":
            raise SecurityViolation(f"Refusing to start container with --network {value}")
        if flag == "--security-opt" and "unconfined" in value:
            raise SecurityViolation(f"Refusing to start container with --security-opt {value}")
    if "docker.sock" in joined:
        raise SecurityViolation("Refusing to mount the Docker socket")
    required = [
        ("--network", "none"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--user", SANDBOX_UID),
    ]
    for flag, value in required:
        if not any(command[i] == flag and command[i + 1] == value for i in range(len(command) - 1)):
            raise SecurityViolation(f"Sandbox flag {flag} {value} is missing")
    if "--read-only" not in command:
        raise SecurityViolation("Sandbox must use a read-only root filesystem")
    mounts = [
        command[i + 1] for i, arg in enumerate(command[:-1]) if arg in ("--mount", "-v", "--volume")
    ]
    allowed = {
        f"type=bind,source={spec.source_dir},target=/src,readonly",
        f"type=bind,source={spec.output_dir},target=/out",
    }
    if set(mounts) - allowed:
        raise SecurityViolation(f"Unexpected mount: {sorted(set(mounts) - allowed)}")


def _docker_env() -> dict[str, str]:
    # HOME is required for the Docker CLI to find its context (Docker Desktop socket).
    return minimal_env(keep_home=True)
