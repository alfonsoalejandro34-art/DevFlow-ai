from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from devflow.ai.base import AnalysisInput, AnalysisOutput, Provider
from devflow.ai.registry import build_providers
from devflow.app import create_app
from devflow.config import PROJECT_ROOT, Settings
from devflow.runners.base import Availability, Runner

EXAMPLES = PROJECT_ROOT / "examples"
TERMINAL = {"passed", "failed", "error", "timeout", "cancelled"}


class FakeAIProvider:
    """Stands in for an external AI CLI. Returns a configurable diagnosis/patch."""

    name = "claude-code"
    label = "Fake AI provider (tests)"
    is_ai = True
    generates_patches = True

    def __init__(self) -> None:
        self.patch: str | None = None
        self.available = True
        self.requests: list[AnalysisInput] = []

    def availability(self) -> Availability:
        return Availability(self.available, None if self.available else "not installed")

    def analyze(self, request: AnalysisInput, workspace: Path) -> AnalysisOutput:
        self.requests.append(request)
        self.workspace_listing = sorted(
            p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()
        )
        return AnalysisOutput(
            "fake root cause", "fake explanation", "medium", self.patch, "fake rationale"
        )


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "runner": "local",
        "allow_unsandboxed_runner": True,
        "otel_exporter": "none",
        "log_json": False,
        "examples_dir": EXAMPLES,
        "frontend_dist": tmp_path / "no-frontend",
        "api_token": "test-token",
        "max_concurrent_jobs": 4,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def fake_ai() -> FakeAIProvider:
    return FakeAIProvider()


@pytest.fixture
def client_factory(tmp_path: Path, fake_ai: FakeAIProvider) -> Iterator[Any]:
    clients: list[TestClient] = []

    def factory(runner: Runner | None = None, **overrides: Any) -> TestClient:
        settings = make_settings(tmp_path, **overrides)
        providers: dict[str, Provider] = build_providers(settings)
        providers["claude-code"] = fake_ai
        app = create_app(settings, runner=runner, providers=providers)
        client = TestClient(app, headers={"Authorization": "Bearer test-token"})
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory: Any) -> TestClient:
    c: TestClient = client_factory()
    return c


@pytest.fixture
def examples(client: TestClient) -> dict[str, int]:
    response = client.post("/api/repositories/examples")
    assert response.status_code == 201, response.text
    return {repo["name"]: repo["id"] for repo in response.json()}


def wait_for(client: TestClient, path: str, done: Any, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body: dict[str, Any] = client.get(path).json()
        if done(body):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting on {path}: {body}")
        time.sleep(0.1)


def wait_run(client: TestClient, run_id: int, timeout: float = 60) -> dict[str, Any]:
    return wait_for(client, f"/api/runs/{run_id}", lambda b: b["status"] in TERMINAL, timeout)


def start_run(client: TestClient, repo_id: int, revision: str = "HEAD") -> dict[str, Any]:
    response = client.post("/api/runs", json={"repository_id": repo_id, "revision": revision})
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


def git(repo: Path, *args: str) -> str:
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": "/usr/bin:/bin",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


def make_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "init")
    return path


docker_available = shutil.which("docker") is not None
