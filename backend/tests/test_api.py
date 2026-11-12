"""API contract tests: authentication, host checks, validation, error format, settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_repo
from devflow.config import Settings
from fastapi.testclient import TestClient


def test_health_is_public_and_reports_runner(client: TestClient) -> None:
    body = TestClient(client.app).get("/api/health").json()
    assert body["database"] is True
    assert body["runner"]["name"] == "local" and body["runner"]["sandboxed"] is False
    assert any("Unsandboxed" in w["message"] for w in body["warnings"])


@pytest.mark.parametrize(
    "header", [None, "Bearer wrong", "Basic test-token", "test-token", "Bearer "]
)
def test_api_requires_token(client: TestClient, header: str | None) -> None:
    anon = TestClient(client.app)
    headers = {"Authorization": header} if header is not None else {}
    response = anon.get("/api/repositories", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert anon.post("/api/runs", json={"repository_id": 1}, headers=headers).status_code == 401


def test_rejects_untrusted_host_header(client: TestClient) -> None:
    # Defends against DNS rebinding: a page on evil.example resolving to 127.0.0.1.
    response = client.get("/api/repositories", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_correlation_id_round_trip(client: TestClient) -> None:
    response = client.get("/api/repositories", headers={"X-Correlation-ID": "abc12345-test"})
    assert response.headers["X-Correlation-ID"] == "abc12345-test"
    generated = client.get("/api/repositories", headers={"X-Correlation-ID": "bad id with spaces!"})
    assert generated.headers["X-Correlation-ID"] != "bad id with spaces!"
    assert generated.headers["X-Frame-Options"] == "DENY"


def test_error_format_and_status_codes(client: TestClient) -> None:
    missing = client.get("/api/runs/999")
    assert missing.status_code == 404
    assert set(missing.json()["error"]) == {"code", "message", "correlation_id"}
    assert client.post("/api/runs", json={"repository_id": "abc"}).status_code == 422
    assert client.get("/api/runs", params={"limit": 10_000}).status_code == 422
    assert client.get("/api/runs", params={"status": "bogus"}).status_code == 422


@pytest.mark.parametrize(
    "path", ["/etc", "/", "../../etc", "~/.ssh", "/Users", "relative/repo", "/tmp/x\x00y"]
)
def test_register_malicious_paths(client: TestClient, path: str) -> None:
    response = client.post("/api/repositories", json={"path": path})
    assert response.status_code in (403, 422), response.text
    assert client.get("/api/repositories").json() == []


def test_register_requires_approved_root(client: TestClient, tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path / "elsewhere" / "proj", {"tests/test_a.py": "def test_a():\n    assert True\n"}
    )
    denied = client.post("/api/repositories", json={"path": str(repo)})
    assert denied.status_code == 403
    config = client.get("/api/settings").json()["runtime"]
    updated = client.put(
        "/api/settings", json={**config, "repository_roots": [str(tmp_path / "elsewhere")]}
    )
    assert updated.status_code == 200
    created = client.post("/api/repositories", json={"path": str(repo)})
    assert created.status_code == 201
    body = created.json()
    assert body["languages"] == {"Python": 100.0}
    assert body["structure"]["test_framework"] == "pytest"
    assert client.post("/api/repositories", json={"path": str(repo)}).status_code == 409
    detail = client.get(f"/api/repositories/{body['id']}").json()
    assert detail["branch"] == "main" and len(detail["recent_commits"]) == 1


def test_unsupported_repository_cannot_run(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path / "data" / "repos" / "jsproj", {"index.js": "console.log(1)\n"})
    body = client.post("/api/repositories", json={"path": str(repo)}).json()
    assert body["structure"]["supported"] is False
    response = client.post("/api/runs", json={"repository_id": body["id"]})
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("test_timeout_seconds", 0),
        ("test_timeout_seconds", 100000),
        ("memory_mb", 64),
        ("cpus", 64),
        ("ai_provider", "rm -rf /"),
        ("repository_roots", ["/"]),
        ("repository_roots", [str(Path.home())]),
    ],
)
def test_settings_bounds(client: TestClient, field: str, value: object) -> None:
    config = client.get("/api/settings").json()["runtime"]
    response = client.put("/api/settings", json={**config, field: value})
    assert response.status_code in (403, 422), response.text


def test_settings_cannot_enable_network_or_change_runner(client: TestClient) -> None:
    config = client.get("/api/settings").json()
    assert config["static"]["network_access"].startswith("disabled")
    client.put("/api/settings", json={**config["runtime"], "network": True, "runner": "host"})
    after = client.get("/api/settings").json()
    assert "network" not in after["runtime"] and after["static"]["runner"] == "local"


def test_logs_endpoint_validates_stream(client: TestClient) -> None:
    assert client.get("/api/runs/1/logs", params={"stream": "../../etc/passwd"}).status_code == 422


def test_metrics_endpoints(client: TestClient) -> None:
    overview = client.get("/api/metrics/overview").json()
    assert overview["runs"]["total"] == 0 and overview["runs"]["pass_rate"] is None
    assert client.get("/api/metrics/timeseries").json()["daily"] == []
    prom = TestClient(client.app).get("/metrics")
    assert prom.status_code == 200 and "devflow_http_request_duration_seconds" in prom.text


def test_openapi_documents_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for route in (
        "/api/runs",
        "/api/runs/{run_id}/cancel",
        "/api/patches/{patch_id}/review",
        "/api/analyses",
    ):
        assert route in paths


def test_remote_bind_requires_opt_in(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        Settings(_env_file=None, host="0.0.0.0", data_dir=tmp_path)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="UNSANDBOXED"):
        Settings(_env_file=None, runner="local", data_dir=tmp_path)  # type: ignore[call-arg]
