"""End-to-end workflow tests: real git repositories, real pytest executions through the
job system (local runner), real patch application. Only the AI provider is faked."""

from __future__ import annotations

import time
from pathlib import Path

from conftest import FakeAIProvider, git, make_repo, start_run, wait_for, wait_run
from fastapi.testclient import TestClient


def test_passing_repository(client: TestClient, examples: dict[str, int]) -> None:
    run = wait_run(client, start_run(client, examples["calculator-passing"])["id"])
    assert run["status"] == "passed"
    assert (run["total"], run["passed"], run["failed"]) == (3, 3, 0)
    assert run["sandboxed"] is False and run["runner"] == "local"
    assert run["revision"]["sha"] == run["source_info"]["sha"]
    results = client.get(f"/api/runs/{run['id']}/results").json()
    assert {r["outcome"] for r in results} == {"passed"}
    assert all(r["file"] == "tests/test_calc.py" for r in results)


def test_failing_repository_records_classified_failures(
    client: TestClient, examples: dict[str, int]
) -> None:
    run = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    assert run["status"] == "failed"
    assert (run["total"], run["passed"], run["failed"]) == (14, 9, 5)
    failures = client.get(f"/api/runs/{run['id']}/failures").json()
    by_node = {f["nodeid"]: f for f in failures}
    zero_div = by_node["tests/test_analytics.py::test_average_order_value_without_orders"]
    assert zero_div["category"] == "exception"
    assert zero_div["exception_type"] == "ZeroDivisionError"
    assert zero_div["file"] == "inventory/analytics.py" and zero_div["line"] == 10
    assert "ZeroDivisionError" in zero_div["traceback"]
    discount = by_node["tests/test_pricing.py::test_discount_reduces_price"]
    assert discount["category"] == "assertion"
    assert "50.0 == 150.0" in discount["summary"] or "assert" in discount["summary"]
    stdout = client.get(f"/api/runs/{run['id']}/logs", params={"stream": "stdout"}).json()
    assert "5 failed, 9 passed" in stdout["content"]


def test_syntax_error_repository(client: TestClient, examples: dict[str, int]) -> None:
    run = wait_run(client, start_run(client, examples["syntax-error-demo"])["id"])
    assert run["status"] == "failed"
    failures = client.get(f"/api/runs/{run['id']}/failures").json()
    assert failures and failures[0]["category"] == "syntax_error"
    assert failures[0]["exception_type"] == "SyntaxError"
    assert "SyntaxError" in failures[0]["summary"]  # not pytest's generic "collection failure"
    assert failures[0]["nodeid"] == "tests/test_parser.py"


def test_missing_dependency_repository(client: TestClient, examples: dict[str, int]) -> None:
    run = wait_run(client, start_run(client, examples["missing-dependency-demo"])["id"])
    assert run["status"] == "failed"
    failure = client.get(f"/api/runs/{run['id']}/failures").json()[0]
    assert failure["category"] == "missing_dependency"
    assert "shapely" in failure["summary"]


def test_hanging_test_is_stopped_by_per_test_timeout(
    client: TestClient, examples: dict[str, int]
) -> None:
    config = client.get("/api/settings").json()["runtime"]
    assert (
        client.put("/api/settings", json={**config, "test_timeout_seconds": 6}).status_code == 200
    )
    run = wait_run(client, start_run(client, examples["timeout-demo"])["id"], timeout=30)
    assert run["status"] == "failed"
    assert run["passed"] == 1
    failure = client.get(f"/api/runs/{run['id']}/failures").json()[0]
    assert failure["category"] == "timeout"


def test_wall_clock_timeout_kills_run(client: TestClient, tmp_path: Path) -> None:
    # This test disables pytest-timeout's SIGALRM, so only DevFlow's wall clock can stop it.
    repo = make_repo(
        tmp_path / "data" / "repos" / "stubborn",
        {
            "tests/test_block.py": "import signal, time\n\ndef test_block():\n"
            "    signal.signal(signal.SIGALRM, signal.SIG_IGN)\n    time.sleep(120)\n"
        },
    )
    repo_id = client.post("/api/repositories", json={"path": str(repo)}).json()["id"]
    config = client.get("/api/settings").json()["runtime"]
    client.put("/api/settings", json={**config, "test_timeout_seconds": 5})
    started = time.monotonic()
    run = wait_run(client, start_run(client, repo_id)["id"], timeout=30)
    assert run["status"] == "timeout"
    assert time.monotonic() - started < 20
    assert client.get(f"/api/runs/{run['id']}/failures").json()[0]["category"] == "timeout"


def test_cancel_running_job(client: TestClient, examples: dict[str, int]) -> None:
    run = start_run(client, examples["timeout-demo"])
    wait_for(client, f"/api/runs/{run['id']}", lambda b: b["status"] == "running")
    time.sleep(0.5)
    response = client.post(f"/api/runs/{run['id']}/cancel")
    assert response.status_code == 200
    final = wait_run(client, run["id"], timeout=20)
    assert final["status"] == "cancelled"
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 409


def test_duplicate_active_run_is_rejected(client: TestClient, examples: dict[str, int]) -> None:
    first = start_run(client, examples["timeout-demo"])
    duplicate = client.post(
        "/api/runs", json={"repository_id": examples["timeout-demo"], "revision": "HEAD"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    client.post(f"/api/runs/{first['id']}/cancel")
    wait_run(client, first["id"])
    # Once the first run finished, the same revision can be run again.
    second = start_run(client, examples["timeout-demo"])
    client.post(f"/api/runs/{second['id']}/cancel")
    wait_run(client, second["id"])


def test_run_specific_revision_and_compare(client: TestClient, tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path / "data" / "repos" / "evolving",
        {
            "mod.py": "def f():\n    return 1\n",
            "tests/test_mod.py": "from mod import f\n\ndef test_f():\n    assert f() == 2\n",
        },
    )
    first_sha = git(repo, "rev-parse", "HEAD").strip()
    (repo / "mod.py").write_text("def f():\n    return 2\n")
    git(repo, "commit", "-qam", "fix")
    repo_id = client.post("/api/repositories", json={"path": str(repo)}).json()["id"]

    old = wait_run(client, start_run(client, repo_id, first_sha)["id"])
    new = wait_run(client, start_run(client, repo_id, "main")["id"])
    assert (old["status"], new["status"]) == ("failed", "passed")
    comparison = client.get(f"/api/runs/{new['id']}/compare").json()
    assert comparison["baseline_run_id"] == old["id"]
    assert comparison["fixed"] == ["tests/test_mod.py::test_f"]
    # The earlier failure is now recorded as resolved by the newer run.
    failure = client.get(f"/api/runs/{old['id']}/failures").json()[0]
    assert failure["resolved_by_run_id"] == new["id"]
    assert (
        client.post(
            "/api/runs", json={"repository_id": repo_id, "revision": "no-such-branch"}
        ).status_code
        == 422
    )


def test_demo_patch_approve_retest_and_export(client: TestClient, examples: dict[str, int]) -> None:
    repo_id = examples["inventory-service"]
    run = wait_run(client, start_run(client, repo_id)["id"])
    analysis = client.post("/api/analyses", json={"run_id": run["id"], "provider": "demo"}).json()
    analysis = wait_for(
        client,
        f"/api/analyses/{analysis['id']}",
        lambda b: b["status"] not in ("pending", "running"),
    )
    assert analysis["status"] == "completed", analysis
    assert analysis["is_ai"] is False
    assert analysis["root_cause"].startswith("[SCRIPTED DEMO - NOT AI]")
    patch = client.get(f"/api/patches/{analysis['patch_ids'][0]}").json()
    assert patch["status"] == "proposed" and patch["validation_errors"] == []
    assert set(patch["files"]) == {
        "inventory/pricing.py",
        "inventory/stock.py",
        "inventory/analytics.py",
    }

    repo_path = Path(client.get(f"/api/repositories/{repo_id}").json()["path"])
    before = (repo_path / "inventory/pricing.py").read_text()

    # Export is refused before approval and retest.
    assert (
        client.post(f"/api/patches/{patch['id']}/export", json={"confirm": True}).status_code == 409
    )

    reviewed = client.post(
        f"/api/patches/{patch['id']}/review", json={"decision": "approve", "comment": "lgtm"}
    ).json()
    assert reviewed["status"] == "retesting"
    retest = wait_run(client, reviewed["retest_run_id"])
    assert retest["status"] == "passed" and retest["kind"] == "patch_retest"
    # The verdict is recorded when the retest finishes, before anyone reads the patch.
    assert client.get("/api/metrics/overview").json()["ai"]["retest_passed"] == 1
    assert retest["parent_run_id"] == run["id"]
    patch = client.get(f"/api/patches/{patch['id']}").json()
    assert patch["status"] == "retest_passed"
    comparison = client.get(f"/api/runs/{retest['id']}/compare").json()
    assert len(comparison["fixed"]) == 5 and comparison["regressed"] == []

    # Approval and retest never touched the user's repository.
    assert (repo_path / "inventory/pricing.py").read_text() == before
    assert (
        client.post(f"/api/patches/{patch['id']}/review", json={"decision": "reject"}).status_code
        == 409
    )

    assert (
        client.post(f"/api/patches/{patch['id']}/export", json={"confirm": False}).status_code
        == 422
    )
    exported = client.post(f"/api/patches/{patch['id']}/export", json={"confirm": True}).json()
    assert exported["status"] == "exported"
    assert exported["exported_branch"] == f"devflow/patch-{patch['id']}"
    # A new branch exists; the working tree and current branch are unchanged.
    assert git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert (repo_path / "inventory/pricing.py").read_text() == before
    assert git(repo_path, "status", "--porcelain").strip() == ""
    fixed = git(repo_path, "show", f"{exported['exported_branch']}:inventory/pricing.py")
    assert "(1 - percent / 100)" in fixed

    overview = client.get("/api/metrics/overview").json()
    assert overview["ai"]["approved"] == 1 and overview["ai"]["retest_passed"] == 1
    assert overview["productivity"]["resolved_failures"] == 5


def test_rejected_ai_proposal(
    client: TestClient, examples: dict[str, int], fake_ai: FakeAIProvider
) -> None:
    run = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    fake_ai.patch = (
        Path(__file__).parents[2] / "examples/demo-fixes/inventory-service/analytics.patch"
    ).read_text()
    analysis = client.post(
        "/api/analyses", json={"run_id": run["id"], "provider": "claude-code"}
    ).json()
    analysis = wait_for(
        client, f"/api/analyses/{analysis['id']}", lambda b: b["status"] == "completed"
    )
    assert analysis["is_ai"] is True
    # The provider only received the files referenced by the failures, not the repo.
    sent = set(fake_ai.requests[0].files)
    assert "inventory/analytics.py" in sent and "README.md" not in sent
    assert set(fake_ai.workspace_listing) == sent
    patch_id = analysis["patch_ids"][0]
    rejected = client.post(
        f"/api/patches/{patch_id}/review", json={"decision": "reject", "comment": "prefer raising"}
    ).json()
    assert rejected["status"] == "rejected" and rejected["retest_run_id"] is None
    assert rejected["decisions"][0]["decision"] == "reject"
    assert client.get("/api/metrics/overview").json()["ai"]["ai_acceptance_rate"] == 0.0


def test_approved_patch_that_still_fails(
    client: TestClient, examples: dict[str, int], fake_ai: FakeAIProvider
) -> None:
    run = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    # Fixes only one of four bugs: the retest must still fail.
    fake_ai.patch = (
        Path(__file__).parents[2] / "examples/demo-fixes/inventory-service/stock.patch"
    ).read_text()
    analysis = client.post("/api/analyses", json={"run_id": run["id"]}).json()  # default provider
    assert analysis["provider"] == "heuristic"
    analysis = client.post(
        "/api/analyses", json={"run_id": run["id"], "provider": "claude-code"}
    ).json()
    analysis = wait_for(
        client, f"/api/analyses/{analysis['id']}", lambda b: b["status"] == "completed"
    )
    patch = client.post(
        f"/api/patches/{analysis['patch_ids'][0]}/review", json={"decision": "approve"}
    ).json()
    retest = wait_run(client, patch["retest_run_id"])
    assert retest["status"] == "failed" and retest["failed"] == 4
    assert client.get(f"/api/patches/{patch['id']}").json()["status"] == "retest_failed"
    comparison = client.get(f"/api/runs/{retest['id']}/compare").json()
    assert comparison["fixed"] == ["tests/test_stock.py::test_cannot_remove_more_than_available"]
    assert len(comparison["still_failing"]) == 4


def test_patch_that_does_not_apply_is_marked_invalid(
    client: TestClient, examples: dict[str, int], fake_ai: FakeAIProvider
) -> None:
    run = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    fake_ai.patch = (
        "diff --git a/inventory/stock.py b/inventory/stock.py\n--- a/inventory/stock.py\n+++ b/inventory/stock.py\n"
        "@@ -1,3 +1,3 @@\n-this line does not exist\n+replacement\n context\n"
    )
    analysis = client.post(
        "/api/analyses", json={"run_id": run["id"], "provider": "claude-code"}
    ).json()
    analysis = wait_for(
        client, f"/api/analyses/{analysis['id']}", lambda b: b["status"] == "completed"
    )
    patch = client.get(f"/api/patches/{analysis['patch_ids'][0]}").json()
    assert patch["status"] == "invalid"
    assert any("does not apply" in e for e in patch["validation_errors"])
    assert (
        client.post(f"/api/patches/{patch['id']}/review", json={"decision": "approve"}).status_code
        == 409
    )


def test_malicious_patch_from_provider_is_rejected(
    client: TestClient, examples: dict[str, int], fake_ai: FakeAIProvider
) -> None:
    run = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    fake_ai.patch = "--- a/../../../.ssh/authorized_keys\n+++ b/../../../.ssh/authorized_keys\n@@ -0,0 +1 @@\n+ssh-rsa AAAA attacker\n"
    analysis = client.post(
        "/api/analyses", json={"run_id": run["id"], "provider": "claude-code"}
    ).json()
    analysis = wait_for(
        client, f"/api/analyses/{analysis['id']}", lambda b: b["status"] == "completed"
    )
    patch = client.get(f"/api/patches/{analysis['patch_ids'][0]}").json()
    assert patch["status"] == "invalid"
    assert any("Unsafe path" in e for e in patch["validation_errors"])


def test_unavailable_ai_provider_degrades_gracefully(
    client: TestClient, examples: dict[str, int], fake_ai: FakeAIProvider
) -> None:
    run = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    fake_ai.available = False
    analysis = client.post(
        "/api/analyses", json={"run_id": run["id"], "provider": "claude-code"}
    ).json()
    assert analysis["status"] == "unavailable"
    assert "heuristic" in analysis["error"]
    # The non-AI fallback still works.
    fallback = client.post(
        "/api/analyses", json={"run_id": run["id"], "provider": "heuristic"}
    ).json()
    fallback = wait_for(
        client, f"/api/analyses/{fallback['id']}", lambda b: b["status"] == "completed"
    )
    assert fallback["is_ai"] is False and fallback["patch_ids"] == []
    assert "ZeroDivisionError" in fallback["explanation"] or "assertion" in fallback["root_cause"]


def test_ai_disabled_in_settings(client: TestClient, examples: dict[str, int]) -> None:
    run = wait_run(client, start_run(client, examples["calculator-passing"])["id"])
    assert client.post("/api/analyses", json={"run_id": run["id"]}).status_code == 409  # passed run
    config = client.get("/api/settings").json()["runtime"]
    client.put("/api/settings", json={**config, "ai_enabled": False})
    providers = {p["name"]: p for p in client.get("/api/providers").json()}
    assert providers["claude-code"]["available"] is False
    assert providers["heuristic"]["available"] is True
    failing = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    analysis = client.post(
        "/api/analyses", json={"run_id": failing["id"], "provider": "claude-code"}
    ).json()
    assert analysis["status"] == "unavailable" and "disabled" in analysis["error"]


def test_interrupted_runs_are_recovered_on_restart(client_factory) -> None:  # type: ignore[no-untyped-def]
    first = client_factory()
    repos = first.post("/api/repositories/examples").json()
    run = start_run(first, next(r["id"] for r in repos if r["name"] == "timeout-demo"))
    wait_for(first, f"/api/runs/{run['id']}", lambda b: b["status"] == "running")
    # Simulate a crash: a second app instance on the same database starts up.
    second = client_factory()
    recovered = second.get(f"/api/runs/{run['id']}").json()
    assert recovered["status"] == "error"
    assert "Interrupted" in recovered["error_message"]


def test_pytest_config_is_never_inherited_from_parent_directories(
    client_factory, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    # Regression: with the data dir inside another project, pytest used to walk up and
    # apply that project's configuration (here, options that break every run).
    parent = tmp_path / "outer-project"
    parent.mkdir()
    (parent / "pytest.ini").write_text(
        "[pytest]\naddopts = --definitely-not-an-option\ntestpaths = nowhere\n"
    )
    client = client_factory(data_dir=parent / "data")
    repos = {r["name"]: r["id"] for r in client.post("/api/repositories/examples").json()}
    for name, expected in (("calculator-passing", "passed"), ("missing-dependency-demo", "failed")):
        run = wait_run(client, start_run(client, repos[name])["id"])
        assert run["status"] == expected, run
    failure = client.get(f"/api/runs/{run['id']}/failures").json()[0]
    assert failure["nodeid"] == "tests/test_geo.py", failure["nodeid"]


def test_analytics_reflect_recorded_activity(
    client: TestClient, examples: dict[str, int], settings
) -> None:  # type: ignore[no-untyped-def]
    from devflow.database.models import BenchmarkResult
    from devflow.database.session import Database

    wait_run(client, start_run(client, examples["calculator-passing"])["id"])
    failing = wait_run(client, start_run(client, examples["inventory-service"])["id"])
    series = client.get("/api/metrics/timeseries", params={"days": 7}).json()
    assert len(series["daily"]) == 1
    day = series["daily"][0]
    assert (day["runs"], day["passed"], day["failed"], day["pass_rate"]) == (2, 1, 1, 50.0)
    assert day["failures_by_category"] == {"assertion": 4, "exception": 1}
    assert [r["id"] for r in series["runs"]][-1] == failing["id"]
    overview = client.get("/api/metrics/overview").json()
    assert overview["runs"]["pass_rate"] == 50.0
    assert overview["productivity"]["failure_categories"] == {"assertion": 4, "exception": 1}
    assert overview["productivity"]["avg_time_to_failure_ms"] == failing["duration_ms"]

    db = Database(settings.sqlalchemy_url)
    with db.transaction() as session:
        for i, ms in enumerate((100.0, 300.0, 200.0)):
            session.add(
                BenchmarkResult(
                    batch_id="b1",
                    scenario="s",
                    mode="baseline",
                    iteration=i,
                    duration_ms=ms,
                    success=True,
                )
            )
    db.dispose()
    bench = client.get("/api/benchmarks").json()
    assert (
        bench[0]["median_ms"] == 200.0
        and bench[0]["iterations"] == 3
        and bench[0]["min_ms"] == 100.0
    )
