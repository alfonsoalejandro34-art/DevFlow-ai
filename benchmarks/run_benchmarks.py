"""Benchmark: manual developer workflow (baseline) vs the DevFlow workflow.

    python benchmarks/run_benchmarks.py [--iterations 10] [--warmup 1]

Requires a running DevFlow server. Scenarios use the bundled example repositories so
every run is identical. Methodology is documented in docs/BENCHMARKS.md.

Scenarios
---------
test_feedback/<repo>
    baseline: clone the repository at HEAD into a temp directory and run pytest
              directly with the DevFlow virtualenv's interpreter (what a developer
              does by hand). Time = start until pytest exits.
    devflow:  POST /api/runs, poll until the run is finished AND classified failure
              records are available. Time = request until results are queryable.
fix_validation/inventory-service
    baseline: clone, `git apply` the known fix, run pytest.
    devflow:  propose the same fix through the scripted demo provider, approve it,
              and wait for the sandboxed retest verdict. Time = approval until verdict.
container_startup (only with the Docker runner)
    `docker run` of the sandbox image executing `python -c pass` with the same
    security flags DevFlow uses; isolates container start/teardown cost.

Results are written to the database (shown on the Analytics page) and to
benchmarks/results/<batch>.json. Nothing is extrapolated: if DevFlow is slower in a
scenario, the numbers say so.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from devflow.config import PROJECT_ROOT, get_settings
from devflow.database.models import BenchmarkResult
from devflow.database.session import Database
from devflow.security.auth import load_or_create_token

TERMINAL = {"passed", "failed", "error", "timeout", "cancelled"}
POLL_SECONDS = 0.05
FIX_PATCHES = PROJECT_ROOT / "examples" / "demo-fixes" / "inventory-service"


def git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )


def baseline_tests(repo: Path, patch: str | None = None) -> tuple[float, bool]:
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        start = time.perf_counter()
        git("clone", "-q", "--no-hardlinks", str(repo), str(clone))
        if patch:
            subprocess.run(
                ["git", "apply", "-"],
                cwd=clone,
                input=patch.encode(),
                check=True,
                capture_output=True,
            )
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=clone,
            capture_output=True,
            check=False,
        )
        elapsed = time.perf_counter() - start
    return elapsed * 1000, result.returncode in (0, 1)


def poll(client: httpx.Client, path: str, done: Any, timeout: float = 600) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body: dict[str, Any] = client.get(path).raise_for_status().json()
        if done(body):
            return body
        if time.monotonic() > deadline:
            raise TimeoutError(path)
        time.sleep(POLL_SECONDS)


def devflow_tests(client: httpx.Client, repo_id: int) -> tuple[float, bool, dict[str, Any]]:
    start = time.perf_counter()
    run = client.post("/api/runs", json={"repository_id": repo_id}).raise_for_status().json()
    run = poll(client, f"/api/runs/{run['id']}", lambda b: b["status"] in TERMINAL)
    failures = client.get(f"/api/runs/{run['id']}/failures").json()
    elapsed = (time.perf_counter() - start) * 1000
    ok = run["status"] in ("passed", "failed")
    return (
        elapsed,
        ok,
        {
            "run_id": run["id"],
            "status": run["status"],
            "failures": len(failures),
            "test_duration_ms": run["test_duration_ms"],
            "overhead_ms": run["overhead_ms"],
        },
    )


def devflow_fix(client: httpx.Client, run_id: int) -> tuple[float, bool, dict[str, Any]]:
    analysis = (
        client.post("/api/analyses", json={"run_id": run_id, "provider": "demo"})
        .raise_for_status()
        .json()
    )
    analysis = poll(
        client,
        f"/api/analyses/{analysis['id']}",
        lambda b: b["status"] not in ("pending", "running"),
    )
    patch_id = analysis["patch_ids"][0]
    start = time.perf_counter()
    patch = client.post(
        f"/api/patches/{patch_id}/review", json={"decision": "approve", "comment": "benchmark"}
    ).json()
    run = poll(client, f"/api/runs/{patch['retest_run_id']}", lambda b: b["status"] in TERMINAL)
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed, run["status"] == "passed", {"patch_id": patch_id, "retest_run_id": run["id"]}


def container_startup(image: str) -> tuple[float, bool]:
    start = time.perf_counter()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            image,
            "python",
            "-c",
            "pass",
        ],
        capture_output=True,
        check=False,
    )
    return (time.perf_counter() - start) * 1000, result.returncode == 0


def summarize(samples: list[float]) -> dict[str, float | None]:
    ordered = sorted(samples)
    if not ordered:
        return {"median": None, "p90": None, "min": None, "max": None, "stdev": None}
    return {
        "median": round(statistics.median(ordered), 1),
        "p90": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 1),
        "min": round(ordered[0], 1),
        "max": round(ordered[-1], 1),
        "stdev": round(statistics.stdev(ordered), 1) if len(ordered) > 1 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=1)
    args = parser.parse_args()

    settings = get_settings()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{settings.port}",
        headers={
            "Authorization": f"Bearer {load_or_create_token(settings.resolved_data_dir)}",
            "X-DevFlow-Client": "bench",
        },
        timeout=60,
    )
    health = client.get("/api/health").json()
    if not health["runner"]["available"]:
        print(f"Runner unavailable: {health['runner']['reason']}")
        return 1
    runner = health["runner"]["name"]
    repos = {
        r["name"]: r for r in client.post("/api/repositories/examples").raise_for_status().json()
    }
    batch = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rows: list[dict[str, Any]] = []

    def measure(scenario: str, mode: str, fn: Any) -> None:
        for i in range(args.warmup + args.iterations):
            outcome = fn()
            elapsed, ok = outcome[0], outcome[1]
            details = outcome[2] if len(outcome) > 2 else {}
            if i < args.warmup:
                continue
            rows.append(
                {
                    "scenario": scenario,
                    "mode": mode,
                    "iteration": i - args.warmup,
                    "duration_ms": round(elapsed, 2),
                    "success": ok,
                    "details": details,
                }
            )
            print(
                f"  {scenario:<34} {mode:<10} #{i - args.warmup:<3} {elapsed:8.1f} ms {'ok' if ok else 'FAILED'}"
            )

    for name in ("calculator-passing", "inventory-service"):
        repo = repos[name]
        measure(f"test_feedback/{name}", "baseline", lambda r=repo: baseline_tests(Path(r["path"])))
        measure(
            f"test_feedback/{name}",
            f"devflow-{runner}",
            lambda r=repo: devflow_tests(client, r["id"]),
        )

    inventory = repos["inventory-service"]
    fix = "".join(p.read_text() for p in sorted(FIX_PATCHES.glob("*.patch")))
    base_run = devflow_tests(client, inventory["id"])[2]["run_id"]
    measure(
        "fix_validation/inventory-service",
        "baseline",
        lambda: baseline_tests(Path(inventory["path"]), fix),
    )
    measure(
        "fix_validation/inventory-service",
        f"devflow-{runner}",
        lambda: devflow_fix(client, base_run),
    )

    if runner == "docker" and shutil.which("docker"):
        measure("container_startup", "docker", lambda: container_startup(settings.runner_image))

    meta = {
        "batch_id": batch,
        "runner": runner,
        "iterations": args.iterations,
        "warmup": args.warmup,
        "poll_interval_ms": POLL_SECONDS * 1000,
        "machine": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
    }
    summary: dict[str, Any] = {}
    for row in rows:
        key = f"{row['scenario']} [{row['mode']}]"
        summary.setdefault(key, []).append(row["duration_ms"])
    report = {
        **meta,
        "summary": {k: {"n": len(v), **summarize(v)} for k, v in summary.items()},
        "samples": rows,
    }
    out = PROJECT_ROOT / "benchmarks" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{batch}.json").write_text(json.dumps(report, indent=2))

    db = Database(settings.sqlalchemy_url)
    with db.transaction() as session:
        for row in rows:
            session.add(
                BenchmarkResult(
                    batch_id=batch,
                    scenario=row["scenario"],
                    mode=row["mode"],
                    iteration=row["iteration"],
                    duration_ms=row["duration_ms"],
                    success=row["success"],
                    details={**row["details"], "runner": runner},
                )
            )
    db.dispose()

    print(f"\nBatch {batch} ({runner} runner, n={args.iterations}, warmup={args.warmup})")
    print(f"{'scenario':<52} {'median':>9} {'p90':>9} {'min':>9} {'max':>9} {'stdev':>8}")
    for key, stats in report["summary"].items():
        print(
            f"{key:<52} "
            + " ".join(
                f"{(stats[s] if stats[s] is not None else float('nan')):>9.1f}"
                for s in ("median", "p90", "min", "max", "stdev")
            )
        )
    print(f"\nSaved benchmarks/results/{batch}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
