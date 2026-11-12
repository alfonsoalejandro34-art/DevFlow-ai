"""Browser end-to-end test: drives the built dashboard against a real DevFlow server.

Nothing is mocked. A uvicorn process serves the API and the production frontend build
from a temporary data directory; Chromium (Playwright) clicks through the full
workflow a developer would follow. AI CLIs are pointed at missing binaries so the test
is deterministic and also checks that unavailable providers degrade gracefully.

    cd frontend && npm run build
    pip install -e '.[e2e]' && playwright install chromium-headless-shell
    pytest -m e2e

On failure a screenshot is written to test-results/e2e/.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from devflow.config import PROJECT_ROOT

playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
expect = playwright.expect

DIST = PROJECT_ROOT / "frontend" / "dist"
ARTIFACTS = PROJECT_ROOT / "test-results" / "e2e"
TOKEN = "e2e-token-not-a-secret"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (DIST / "index.html").is_file(), reason="frontend not built (npm run build)"
    ),
    pytest.mark.timeout(240),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("e2e-data")
    env = {
        **os.environ,
        "DEVFLOW_DATA_DIR": str(data_dir),
        "DEVFLOW_PORT": str(port),
        "DEVFLOW_API_TOKEN": TOKEN,
        "DEVFLOW_RUNNER": "local",
        "DEVFLOW_ALLOW_UNSANDBOXED_RUNNER": "true",
        "DEVFLOW_CLAUDE_BINARY": str(data_dir / "no-claude"),
        "DEVFLOW_CODEX_BINARY": str(data_dir / "no-codex"),
        "DEVFLOW_OTEL_EXPORTER": "none",
        "DEVFLOW_FRONTEND_DIST": str(DIST),
    }
    log = (data_dir / "server.log").open("wb")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "devflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=data_dir,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(
                    "server did not start:\n" + (data_dir / "server.log").read_text()
                )
            time.sleep(0.2)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        log.close()


@pytest.fixture
def page(request: pytest.FixtureRequest) -> Iterator[Any]:
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.set_default_timeout(15_000)
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.console_errors = errors  # type: ignore[attr-defined]
        yield page
        report = getattr(request.node, "rep_call", None)
        if report is not None and report.failed:
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / f"{request.node.name}.png"), full_page=True)
        browser.close()


def nav(page: Any, name: str) -> None:
    page.get_by_role("navigation", name="Main").get_by_role("link", name=name).click()


def test_full_workflow_in_browser(server: str, page: Any) -> None:
    # --- sign in: a wrong token is rejected, the right one opens the dashboard
    page.goto(server)
    page.get_by_label("API token").fill("wrong-token")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_contain_text("Missing or invalid API token")
    page.get_by_label("API token").fill(TOKEN)
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    page.console_errors.clear()  # the deliberate wrong-token attempt logs an expected 401
    expect(page.get_by_text("Unsandboxed local runner is active")).to_be_visible()

    # --- repositories: install examples and start a run on the buggy service
    nav(page, "Repositories")
    page.get_by_role("button", name="Install example repositories").click()
    repo_row = page.get_by_role("row").filter(has_text="inventory-service")
    expect(repo_row).to_be_visible()
    repo_row.click()
    page.get_by_role("button", name="Run tests").click()

    # --- run detail: the run fails with 5 classified failures
    expect(page).to_have_url(re.compile(r"/runs/\d+$"))
    failing_run_url = page.url
    expect(page.locator(".page-header [data-status='failed']")).to_be_visible(timeout=60_000)
    expect(page.get_by_role("tab", name="Failures (5)")).to_be_visible()
    failure = page.locator("details.failure").filter(
        has_text="test_average_order_value_without_orders"
    )
    expect(failure).to_contain_text("Exception")
    failure.locator("summary").click()
    expect(failure.get_by_label("Traceback")).to_contain_text("ZeroDivisionError")

    # --- diagnosis: AI providers are unavailable; the scripted demo is labelled as not AI
    page.get_by_role("tab", name="Diagnosis & patches").click()
    provider = page.get_by_label("Analysis provider")
    expect(provider).to_be_enabled()
    expect(provider.locator("option", has_text="Claude Code CLI")).to_have_text(
        re.compile("unavailable")
    )
    provider.select_option("demo")
    page.get_by_role("button", name="Analyze failures").click()
    expect(page.get_by_text(re.compile(r"\[SCRIPTED DEMO - NOT AI\]"))).to_be_visible()
    expect(page.get_by_text("not AI · demo")).to_be_visible()
    # The candidate was checked in the sandbox before being shown for review.
    expect(page.get_by_text("tests pass in sandbox", exact=True)).to_be_visible()
    expect(page.get_by_text("all tests pass in sandbox")).to_be_visible()

    # --- review: nothing happens until the human approves; approval triggers a retest
    approve = page.get_by_role("button", name="Approve and retest")
    expect(approve).to_be_visible()
    page.get_by_label(re.compile("Review comment")).fill("Approved in browser e2e test")
    approve.click()
    expect(page.get_by_text("all tests pass")).to_be_visible(timeout=60_000)
    retest_link = page.locator("div.row", has_text="Retest:").get_by_role("link")
    retest_link.click()

    # --- retest run: passes, and the comparison shows all five failures fixed
    expect(page.locator(".page-header [data-status='passed']")).to_be_visible(timeout=30_000)
    expect(page.get_by_text(re.compile(r"retest of patch #\d+"))).to_be_visible()
    page.get_by_role("tab", name="Compare").click()
    fixed = page.locator("div.row").filter(
        has=page.locator("[data-status='passed']", has_text="Fixed")
    )
    expect(fixed).to_contain_text("5")

    # --- build history: original run, the AI pre-review check, and the retest
    nav(page, "Build history")
    rows = page.locator("table.table tbody tr")
    expect(rows).to_have_count(3)
    expect(rows.filter(has_text="AI check")).to_have_count(1)
    page.get_by_label("Status filter").select_option("failed")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("inventory-service")
    rows.first.click()
    expect(page).to_have_url(failing_run_url)

    # --- patch reviews and analytics reflect the decision
    nav(page, "Patch reviews")
    page.get_by_label("Patch status filter").select_option("retest_passed")
    expect(page.locator("table.table tbody tr")).to_have_count(1)
    nav(page, "Analytics")
    suggestion = page.locator(".stat").filter(has_text="Suggestion acceptance")
    expect(suggestion).to_contain_text("100%")

    assert page.console_errors == [], page.console_errors


def test_signed_out_user_cannot_reach_data(server: str, page: Any) -> None:
    page.goto(f"{server}/builds")
    expect(page.get_by_role("button", name="Sign in")).to_be_visible()
    expect(page.locator("table.table")).to_have_count(0)
