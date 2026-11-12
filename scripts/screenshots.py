"""Capture dashboard screenshots from the running application into docs/screenshots/.

    PLAYWRIGHT_BROWSERS_PATH=.cache/ms-playwright python scripts/screenshots.py

Requires `pip install playwright && playwright install chromium-headless-shell` and a
running server with the built dashboard (`make serve`). Screenshots show whatever data
is actually in the database.
"""

from __future__ import annotations

import sys

import httpx
from playwright.sync_api import sync_playwright

from devflow.config import PROJECT_ROOT, get_settings
from devflow.security.auth import load_or_create_token

OUT = PROJECT_ROOT / "docs" / "screenshots"


def main() -> int:
    settings = get_settings()
    base = f"http://127.0.0.1:{settings.port}"
    token = load_or_create_token(settings.resolved_data_dir)
    api = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"})
    runs = api.get("/api/runs", params={"limit": 200}).json()["items"]
    analyzed = {a["run_id"] for a in api.get("/api/analyses").json() if a["is_ai"]}
    failing = next((r for r in runs if r["id"] in analyzed), None) or next(
        r for r in runs if r["status"] == "failed"
    )
    retest = next(
        (r for r in runs if r["kind"] == "patch_retest" and r["status"] == "passed"), None
    )
    inventory = next(
        r for r in api.get("/api/repositories").json() if r["name"] == "inventory-service"
    )
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            page = browser.new_page(
                viewport={"width": 1440, "height": 960}, color_scheme=scheme, device_scale_factor=2
            )
            page.add_init_script(f"window.localStorage.setItem('devflow.token', {token!r})")
            suffix = "" if scheme == "light" else "-dark"
            shots: list[tuple[str, str, str | None, bool]] = [("/", "overview", None, True)]
            if scheme == "light":
                shots += [
                    ("/repositories", "repositories", f"row:{inventory['name']}", True),
                    ("/builds", "build-history", None, True),
                    (f"/runs/{failing['id']}", "run-failures", "failure", True),
                    (f"/runs/{failing['id']}", "run-analysis", "tab:Diagnosis & patches", True),
                    ("/reviews", "patch-reviews", None, True),
                    ("/analytics", "analytics", None, True),
                    ("/settings", "settings", None, True),
                ]
                if retest:
                    shots.append((f"/runs/{retest['id']}", "run-compare", "tab:Compare", False))
            for path, name, action, full in shots:
                page.goto(base + path)
                page.wait_for_load_state("networkidle")
                if action and action.startswith("row:"):
                    page.get_by_role("row", name=action[4:]).first.click()
                elif action and action.startswith("tab:"):
                    page.get_by_role("tab", name=action[4:]).click()
                elif action == "failure":
                    page.locator("details.failure").first.locator("summary").click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUT / f"{name}{suffix}.png"), full_page=full)
                print("saved", name + suffix)
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
