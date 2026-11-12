"""Live Docker sandbox tests. Each test runs real pytest workloads inside the runner
container and checks a security property from the inside.

Run with `make test-docker` (needs Docker and `make runner-image`). Skipped otherwise.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import make_repo, start_run, wait_for, wait_run

from devflow.runners.base import RunSpec
from devflow.runners.docker import DockerRunner

IMAGE = "devflow-runner:0.1"


def _docker_ready() -> bool:
    runner = DockerRunner(IMAGE)
    return runner.availability().available


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        not _docker_ready(), reason="Docker engine or devflow-runner image unavailable"
    ),
]

PROBES = """
import os, socket, pathlib, pytest

def test_runs_as_unprivileged_user():
    assert os.getuid() == 10001 and os.getgid() == 10001

def test_network_is_disabled():
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 53), timeout=3)

def test_source_is_read_only():
    with pytest.raises(OSError):
        pathlib.Path("/src/new_file").write_text("x")

def test_root_filesystem_is_read_only():
    with pytest.raises(OSError):
        pathlib.Path("/usr/local/lib/evil").write_text("x")

PSEUDO_FS = {"proc", "sysfs", "tmpfs", "devpts", "mqueue", "cgroup", "cgroup2", "overlay"}
DOCKER_MANAGED = {"/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}

def test_only_expected_host_mounts():
    mounts = [line.split() for line in pathlib.Path("/proc/self/mounts").read_text().splitlines()]
    host = {m[1]: m[3].split(",") for m in mounts if m[2] not in PSEUDO_FS and m[1] not in DOCKER_MANAGED}
    assert set(host) == {"/src", "/out"}, host
    assert "ro" in host["/src"]
    root = next(m for m in mounts if m[1] == "/")
    assert "ro" in root[3].split(",")

def test_no_docker_socket_or_root_home():
    assert not os.path.exists("/var/run/docker.sock")
    assert not os.path.exists("/run/docker.sock")
    assert not os.access("/root", os.R_OK)  # root's home is unreadable to the sandbox user

def test_capabilities_dropped():
    status = pathlib.Path("/proc/self/status").read_text()
    eff = next(l for l in status.splitlines() if l.startswith("CapEff:"))
    assert int(eff.split()[1], 16) == 0
    nnp = next(l for l in status.splitlines() if l.startswith("NoNewPrivs:"))
    assert nnp.split()[1] == "1"

def test_tmp_is_writable_but_noexec():
    p = pathlib.Path("/tmp/x.sh"); p.write_text("#!/bin/sh\\necho hi\\n"); p.chmod(0o755)
    with pytest.raises(PermissionError):
        os.execv(str(p), [str(p)])
"""


def test_sandbox_security_properties(client_factory: Any, tmp_path: Path) -> None:
    client = client_factory(runner=DockerRunner(IMAGE))
    repo = make_repo(tmp_path / "data" / "repos" / "probes", {"tests/test_probes.py": PROBES})
    repo_id = client.post("/api/repositories", json={"path": str(repo)}).json()["id"]
    run = wait_run(client, start_run(client, repo_id)["id"], timeout=180)
    results = {r["name"]: r for r in client.get(f"/api/runs/{run['id']}/results").json()}
    failed = {n: r["message"] for n, r in results.items() if r["outcome"] != "passed"}
    assert not failed, failed
    assert run["status"] == "passed" and run["sandboxed"] is True and run["runner"] == "docker"
    assert run["overhead_ms"] is not None


def test_examples_in_docker(client_factory: Any) -> None:
    client = client_factory(runner=DockerRunner(IMAGE))
    repos = {r["name"]: r["id"] for r in client.post("/api/repositories/examples").json()}
    passing = wait_run(client, start_run(client, repos["calculator-passing"])["id"], timeout=180)
    assert passing["status"] == "passed" and passing["passed"] == 3
    failing = wait_run(client, start_run(client, repos["inventory-service"])["id"], timeout=180)
    assert (failing["status"], failing["passed"], failing["failed"]) == ("failed", 9, 5)
    missing = wait_run(
        client, start_run(client, repos["missing-dependency-demo"])["id"], timeout=180
    )
    assert (
        client.get(f"/api/runs/{missing['id']}/failures").json()[0]["category"]
        == "missing_dependency"
    )


def test_memory_limit_is_enforced(client_factory: Any, tmp_path: Path) -> None:
    client = client_factory(runner=DockerRunner(IMAGE))
    repo = make_repo(
        tmp_path / "data" / "repos" / "hog",
        {
            "tests/test_hog.py": "def test_hog():\n    blocks = [bytearray(64 * 1024 * 1024) for _ in range(16)]\n    assert blocks\n"
        },
    )
    repo_id = client.post("/api/repositories", json={"path": str(repo)}).json()["id"]
    config = client.get("/api/settings").json()["runtime"]
    client.put("/api/settings", json={**config, "memory_mb": 256})
    run = wait_run(client, start_run(client, repo_id)["id"], timeout=180)
    # The OOM killer ends the container (exit 137) or Python raises MemoryError.
    assert run["status"] in ("failed", "error"), run
    assert run["passed"] == 0


def test_cancel_kills_container(client_factory: Any) -> None:
    client = client_factory(runner=DockerRunner(IMAGE))
    repos = {r["name"]: r["id"] for r in client.post("/api/repositories/examples").json()}
    run = start_run(client, repos["timeout-demo"])
    wait_for(client, f"/api/runs/{run['id']}", lambda b: b["status"] == "running", timeout=60)
    time.sleep(2)
    client.post(f"/api/runs/{run['id']}/cancel")
    final = wait_run(client, run["id"], timeout=60)
    assert final["status"] == "cancelled"
    leftovers = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=devflow.run_id={run['id']}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert leftovers == "", "container was not cleaned up"


def test_wall_clock_timeout_in_docker(tmp_path: Path) -> None:
    src, out = tmp_path / "src", tmp_path / "out"
    (src / "tests").mkdir(parents=True)
    out.mkdir()
    (src / "tests" / "test_block.py").write_text(
        "import signal, time\n\ndef test_block():\n    signal.signal(signal.SIGALRM, signal.SIG_IGN)\n    time.sleep(300)\n"
    )
    spec = RunSpec(990001, src, out, timeout_seconds=8, memory_mb=256, cpus=0.5, pids_limit=64)
    started = time.monotonic()
    outcome = DockerRunner(IMAGE).run(spec, threading.Event())
    assert outcome.timed_out and time.monotonic() - started < 40
