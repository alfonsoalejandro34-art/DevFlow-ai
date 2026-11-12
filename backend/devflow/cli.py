"""``devflow`` command-line interface. ``serve`` starts the server; every other command
is a thin client of the REST API, authenticated with the local token."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from typing import Any

import httpx
import typer

from devflow.config import get_settings, is_loopback
from devflow.security.auth import load_or_create_token

app = typer.Typer(
    help="DevFlow AI: sandboxed test runs, failure analysis, reviewed fixes.", no_args_is_help=True
)
repo_app = typer.Typer(help="Manage registered repositories.", no_args_is_help=True)
patch_app = typer.Typer(help="Review proposed patches.", no_args_is_help=True)
app.add_typer(repo_app, name="repo")
app.add_typer(patch_app, name="patch")

_TERMINAL = {"passed", "failed", "error", "timeout", "cancelled"}


def _client() -> httpx.Client:
    settings = get_settings()
    token = load_or_create_token(
        settings.resolved_data_dir,
        settings.api_token.get_secret_value() if settings.api_token else None,
    )
    host = settings.host if is_loopback(settings.host) else "127.0.0.1"
    return httpx.Client(
        base_url=f"http://{host}:{settings.port}",
        headers={"Authorization": f"Bearer {token}", "X-DevFlow-Client": "cli"},
        timeout=30,
    )


def _call(method: str, path: str, **kwargs: Any) -> Any:
    try:
        with _client() as client:
            response = client.request(method, path, **kwargs)
    except httpx.ConnectError:
        typer.secho(
            "DevFlow server is not running. Start it with `devflow serve`.", fg="red", err=True
        )
        raise typer.Exit(2) from None
    if response.status_code >= 400:
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError):
            message = response.text
        typer.secho(f"Error {response.status_code}: {message}", fg="red", err=True)
        raise typer.Exit(1)
    return response.json() if response.content else None


def _print(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


@app.command()
def serve(
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)"),
) -> None:
    """Start the API server (and the built dashboard, if present) on localhost."""
    import uvicorn

    settings = get_settings()
    typer.echo(f"DevFlow AI on http://{settings.host}:{settings.port}  (token: `devflow token`)")
    uvicorn.run(
        "devflow.main:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
        log_config=None,
        proxy_headers=False,
        server_header=False,
    )


@app.command()
def token() -> None:
    """Print the local API token (paste it into the dashboard's sign-in screen)."""
    settings = get_settings()
    typer.echo(
        load_or_create_token(
            settings.resolved_data_dir,
            settings.api_token.get_secret_value() if settings.api_token else None,
        )
    )


@app.command()
def doctor() -> None:
    """Check the local environment: Python, git, Docker, runner image, AI CLIs."""
    settings = get_settings()
    checks: list[tuple[str, bool, str]] = [
        ("python", sys.version_info >= (3, 12), sys.version.split()[0])
    ]
    for name, binary in (
        ("git", "git"),
        ("docker", settings.docker_binary),
        ("claude", settings.claude_binary),
        ("codex", settings.codex_binary),
    ):
        path = shutil.which(binary)
        version = ""
        if path:
            out = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=20, check=False
            )
            version = (
                (out.stdout or out.stderr).strip().splitlines()[0]
                if (out.stdout or out.stderr)
                else ""
            )
        checks.append((name, path is not None, version or "not found"))
    if shutil.which(settings.docker_binary):
        from devflow.runners.docker import DockerRunner

        availability = DockerRunner(settings.runner_image, settings.docker_binary).availability()
        checks.append(
            ("runner image", availability.available, availability.reason or settings.runner_image)
        )
    for name, ok, detail in checks:
        typer.secho(
            f"{'OK ' if ok else 'MISSING'} {name:<13} {detail}", fg="green" if ok else "yellow"
        )


@repo_app.command("list")
def repo_list() -> None:
    for repo in _call("GET", "/api/repositories"):
        typer.echo(f"{repo['id']:>3}  {repo['name']:<28} {repo['path']}")


@repo_app.command("add")
def repo_add(path: str, name: str | None = typer.Option(None)) -> None:
    _print(_call("POST", "/api/repositories", json={"path": path, "name": name}))


@repo_app.command("examples")
def repo_examples() -> None:
    """Install and register the bundled example repositories."""
    for repo in _call("POST", "/api/repositories/examples"):
        typer.echo(f"{repo['id']:>3}  {repo['name']}")


@repo_app.command("show")
def repo_show(repo_id: int) -> None:
    _print(_call("GET", f"/api/repositories/{repo_id}"))


@app.command()
def run(
    repo_id: int,
    revision: str = typer.Option("HEAD", "--rev"),
    wait: bool = typer.Option(True, help="Wait for the run to finish"),
) -> None:
    """Start a test run for a repository."""
    created = _call("POST", "/api/runs", json={"repository_id": repo_id, "revision": revision})
    typer.echo(
        f"Run #{created['id']} queued ({created['runner']}, sandboxed={created['sandboxed']})"
    )
    if not wait:
        return
    current = created
    while current["status"] not in _TERMINAL:
        time.sleep(1)
        current = _call("GET", f"/api/runs/{created['id']}")
    color = "green" if current["status"] == "passed" else "red"
    typer.secho(
        f"Run #{current['id']} {current['status']}: {current['passed']} passed, "
        f"{current['failed'] + current['errors']} failed, {current['duration_ms']} ms",
        fg=color,
    )
    for failure in _call("GET", f"/api/runs/{created['id']}/failures"):
        typer.echo(f"  [{failure['category']}] {failure['nodeid'] or ''} {failure['summary']}")
    if current["status"] != "passed":
        raise typer.Exit(1)


@app.command()
def runs(limit: int = 20, repo_id: int | None = typer.Option(None, "--repo")) -> None:
    """List recent test runs."""
    params: dict[str, Any] = {"limit": limit}
    if repo_id is not None:
        params["repository_id"] = repo_id
    for r in _call("GET", "/api/runs", params=params)["items"]:
        typer.echo(
            f"#{r['id']:<4} {r['status']:<10} {r['repository_name']:<24} "
            f"{(r['source_info'].get('sha') or '')[:8]}  {r['passed']}/{r['total']} passed  {r['duration_ms']} ms"
        )


@app.command()
def cancel(run_id: int) -> None:
    _print(_call("POST", f"/api/runs/{run_id}/cancel"))


@app.command()
def analyze(run_id: int, provider: str | None = typer.Option(None)) -> None:
    """Analyze a failed run (providers: heuristic, claude-code, codex, demo)."""
    record = _call("POST", "/api/analyses", json={"run_id": run_id, "provider": provider})
    while record["status"] in ("pending", "running"):
        time.sleep(1)
        record = _call("GET", f"/api/analyses/{record['id']}")
    _print(record)


@patch_app.command("show")
def patch_show(patch_id: int) -> None:
    patch = _call("GET", f"/api/patches/{patch_id}")
    typer.echo(
        f"Patch #{patch['id']} [{patch['status']}] risk={patch['risk_level']} ({patch['risk_score']})"
    )
    for reason in patch["risk_reasons"]:
        typer.echo(f"  - {reason}")
    for error in patch["validation_errors"]:
        typer.secho(f"  invalid: {error}", fg="red")
    typer.echo(patch["diff"])


@patch_app.command("review")
def patch_review(
    patch_id: int,
    approve: bool = typer.Option(False, "--approve"),
    reject: bool = typer.Option(False, "--reject"),
    comment: str | None = typer.Option(None),
) -> None:
    if approve == reject:
        typer.secho("Pass exactly one of --approve or --reject", fg="red", err=True)
        raise typer.Exit(2)
    _print(
        _call(
            "POST",
            f"/api/patches/{patch_id}/review",
            json={"decision": "approve" if approve else "reject", "comment": comment},
        )
    )


@patch_app.command("export")
def patch_export(
    patch_id: int, branch: str | None = typer.Option(None), yes: bool = typer.Option(False, "--yes")
) -> None:
    """Write an approved, retested patch to a new branch in the repository."""
    if not yes and not typer.confirm("This writes a new branch to your repository. Continue?"):
        raise typer.Exit(1)
    _print(
        _call("POST", f"/api/patches/{patch_id}/export", json={"confirm": True, "branch": branch})
    )


if __name__ == "__main__":
    app()
