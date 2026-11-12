"""Process-level configuration, loaded from environment variables (prefix ``DEVFLOW_``).

Settings that are safe to change at runtime (timeouts, resource limits, AI provider)
live in the database instead; see ``devflow.services.settings``.
"""

from __future__ import annotations

import ipaddress
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOOPBACK_NAMES = {"localhost"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFLOW_", env_file=".env", extra="ignore", frozen=True
    )

    data_dir: Path = PROJECT_ROOT / ".devflow"
    database_url: str | None = None

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    allow_remote_bind: bool = False
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    api_token: SecretStr | None = None

    # Directories under which repositories may be registered, in addition to the
    # managed example directory. Extra roots can also be added at runtime.
    allowed_roots: list[Path] = []

    runner: Literal["docker", "local"] = "docker"
    # The local runner executes tests directly on the host with no sandbox. It can
    # only be enabled from the environment, never through the API.
    allow_unsandboxed_runner: bool = False
    runner_image: str = "devflow-runner:0.1"
    docker_binary: str = Field(default_factory=lambda: _default_docker())
    max_concurrent_jobs: int = Field(default=2, ge=1, le=8)

    claude_binary: str = "claude"
    codex_binary: str = "codex"
    # API keys are stripped from the AI CLI environment unless explicitly allowed,
    # so that analysis never silently incurs metered API charges.
    ai_pass_api_keys: bool = False
    ai_timeout_seconds: int = Field(default=300, ge=10, le=1800)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    otel_exporter: Literal["none", "file", "console", "otlp"] = "file"

    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    examples_dir: Path = PROJECT_ROOT / "examples"

    @field_validator("host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _require_loopback(self) -> Settings:
        if not self.allow_remote_bind and not is_loopback(self.host):
            raise ValueError(
                f"DEVFLOW_HOST={self.host!r} is not a loopback address; "
                "set DEVFLOW_ALLOW_REMOTE_BIND=true to bind elsewhere"
            )
        if self.runner == "local" and not self.allow_unsandboxed_runner:
            raise ValueError(
                "DEVFLOW_RUNNER=local runs tests on the host without isolation; "
                "set DEVFLOW_ALLOW_UNSANDBOXED_RUNNER=true to acknowledge this"
            )
        return self

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir.expanduser().resolve()

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or f"sqlite:///{self.resolved_data_dir / 'devflow.db'}"

    @property
    def managed_repos_dir(self) -> Path:
        return self.resolved_data_dir / "repos"

    @property
    def runs_dir(self) -> Path:
        return self.resolved_data_dir / "runs"

    @property
    def ai_workspaces_dir(self) -> Path:
        return self.resolved_data_dir / "ai-workspaces"


def _default_docker() -> str:
    """`docker` on PATH, else Docker Desktop's per-user install location."""
    if shutil.which("docker"):
        return "docker"
    user_install = Path.home() / ".docker" / "bin" / "docker"
    return str(user_install) if user_install.is_file() else "docker"


def is_loopback(host: str) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
