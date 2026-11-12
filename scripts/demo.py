"""End-to-end demonstration of the DevFlow AI workflow against a running server.

    python scripts/demo.py [--ai claude-code|codex]

Steps: install examples -> run the buggy inventory-service -> inspect classified
failures -> rule-based diagnosis -> (optional) real AI diagnosis -> scripted demo
patch (clearly labelled, NOT AI) -> human approval -> sandboxed retest -> compare ->
metrics. Every step reports PASS, FAIL, or BLOCKED (with the reason); nothing is faked.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

from devflow.config import get_settings
from devflow.security.auth import load_or_create_token

TERMINAL = {"passed", "failed", "error", "timeout", "cancelled"}
results: list[tuple[str, str, str]] = []


def record(step: str, status: str, detail: str = "") -> None:
    results.append((step, status, detail))
    mark = {"PASS": "\033[32mPASS\033[0m", "FAIL": "\033[31mFAIL\033[0m"}.get(
        status, "\033[33mBLOCKED\033[0m"
    )
    print(f"[{mark}] {step}" + (f" - {detail}" if detail else ""))


def wait(client: httpx.Client, path: str, done: Any, timeout: float = 300) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body: dict[str, Any] = client.get(path).raise_for_status().json()
        if done(body):
            return body
        if time.monotonic() > deadline:
            raise TimeoutError(path)
        time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ai",
        choices=["claude-code", "codex"],
        help="also run a real AI provider (uses your CLI subscription)",
    )
    args = parser.parse_args()

    settings = get_settings()
    token = load_or_create_token(settings.resolved_data_dir)
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{settings.port}",
        headers={"Authorization": f"Bearer {token}", "X-DevFlow-Client": "demo"},
        timeout=30,
    )
    try:
        health = client.get("/api/health").json()
    except httpx.ConnectError:
        print("DevFlow is not running. Start it with `make serve` or `devflow serve`.")
        return 2
    runner = health["runner"]
    record(
        "Server healthy",
        "PASS" if health["status"] == "ok" else "FAIL",
        f"runner={runner['name']} sandboxed={runner['sandboxed']}",
    )
    if not runner["available"]:
        record("Runner available", "BLOCKED", runner["reason"])
        return 1

    repos = {
        r["name"]: r for r in client.post("/api/repositories/examples").raise_for_status().json()
    }
    repo = repos["inventory-service"]
    record("1. Register sample repository", "PASS", f"repository #{repo['id']} at {repo['path']}")

    run = client.post("/api/runs", json={"repository_id": repo["id"]}).raise_for_status().json()
    run = wait(client, f"/api/runs/{run['id']}", lambda b: b["status"] in TERMINAL)
    where = "in Docker" if run["sandboxed"] else "with the UNSANDBOXED local runner"
    record(
        "2. Run tests " + where,
        "PASS" if run["status"] == "failed" else "FAIL",
        f"run #{run['id']}: {run['passed']} passed, {run['failed'] + run['errors']} failed in {run['duration_ms']} ms",
    )

    failures = client.get(f"/api/runs/{run['id']}/failures").json()
    record(
        "3. Detect failures",
        "PASS" if len(failures) == 5 else "FAIL",
        ", ".join(sorted({f["category"] for f in failures})),
    )
    record("4. Capture stack traces", "PASS" if all(f["traceback"] for f in failures) else "FAIL")

    heuristic = client.post(
        "/api/analyses", json={"run_id": run["id"], "provider": "heuristic"}
    ).json()
    heuristic = wait(
        client,
        f"/api/analyses/{heuristic['id']}",
        lambda b: b["status"] not in ("pending", "running"),
    )
    record(
        "5a. Rule-based diagnosis (no AI)",
        "PASS" if heuristic["status"] == "completed" else "FAIL",
        heuristic["root_cause"] or heuristic["error"],
    )

    if args.ai:
        ai = client.post("/api/analyses", json={"run_id": run["id"], "provider": args.ai}).json()
        ai = wait(
            client,
            f"/api/analyses/{ai['id']}",
            lambda b: b["status"] not in ("pending", "running"),
            timeout=600,
        )
        if ai["status"] == "completed":
            record(
                f"5b. AI diagnosis ({args.ai})",
                "PASS",
                f"{ai['root_cause'][:120]} ({ai['duration_ms']} ms, {len(ai['patch_ids'])} patch)",
            )
        else:
            record(f"5b. AI diagnosis ({args.ai})", "BLOCKED", ai["error"])
    else:
        record("5b. AI diagnosis", "BLOCKED", "not requested (pass --ai claude-code or --ai codex)")

    demo = client.post("/api/analyses", json={"run_id": run["id"], "provider": "demo"}).json()
    demo = wait(
        client, f"/api/analyses/{demo['id']}", lambda b: b["status"] not in ("pending", "running")
    )
    if not demo["patch_ids"]:
        record("6. Patch proposal", "FAIL", demo.get("error") or "no patch")
        return summary()
    patch = client.get(f"/api/patches/{demo['patch_ids'][0]}").json()
    record(
        "6. Patch proposal (SCRIPTED DEMO, not AI)",
        "PASS" if patch["status"] == "proposed" else "FAIL",
        f"patch #{patch['id']}, risk {patch['risk_level']}, files {', '.join(patch['files'])}",
    )

    patch = client.post(
        f"/api/patches/{patch['id']}/review",
        json={"decision": "approve", "comment": "demo approval"},
    ).json()
    record(
        "7. Human review and approval",
        "PASS" if patch["status"] in ("retesting", "approved") else "FAIL",
        patch["status"],
    )
    if not patch["retest_run_id"]:
        record("8. Retest approved patch", "BLOCKED", "retest did not start")
        return summary()
    retest = wait(client, f"/api/runs/{patch['retest_run_id']}", lambda b: b["status"] in TERMINAL)
    record(
        "8. Apply to isolated snapshot and retest",
        "PASS" if retest["status"] == "passed" else "FAIL",
        f"run #{retest['id']}: {retest['passed']}/{retest['total']} passed",
    )

    comparison = client.get(f"/api/runs/{retest['id']}/compare").json()
    record(
        "9. Compare baseline and final results",
        "PASS" if len(comparison["fixed"]) == 5 and not comparison["regressed"] else "FAIL",
        f"{len(comparison['fixed'])} fixed, {len(comparison['regressed'])} regressed",
    )

    overview = client.get("/api/metrics/overview").json()
    record(
        "10. Metrics recorded",
        "PASS" if overview["runs"]["total"] >= 2 else "FAIL",
        f"runs={overview['runs']['total']} pass_rate={overview['runs']['pass_rate']}% "
        f"resolved={overview['productivity']['resolved_failures']} acceptance={overview['ai']['acceptance_rate']}%",
    )
    print(
        f"\nOpen the dashboard and go to run #{run['id']} and run #{retest['id']} to see these results."
    )
    return summary()


def summary() -> int:
    failed = [r for r in results if r[1] == "FAIL"]
    blocked = [r for r in results if r[1] == "BLOCKED"]
    print(
        f"\n{len(results) - len(failed) - len(blocked)} passed, {len(failed)} failed, {len(blocked)} blocked"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
