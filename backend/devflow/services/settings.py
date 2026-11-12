"""Runtime-editable configuration stored in the ``app_settings`` table.

Bounds are enforced here so that the API cannot, for example, disable the timeout or
grant a container unlimited memory. Network access for test containers is not a
setting: it is always disabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from devflow.config import Settings
from devflow.database.models import AppSetting
from devflow.database.session import Database
from devflow.security.paths import validate_allowed_root

_KEY = "runtime"

AIProviderName = Literal["heuristic", "claude-code", "codex", "demo"]


class RuntimeConfig(BaseModel):
    test_timeout_seconds: int = Field(default=120, ge=5, le=1800)
    memory_mb: int = Field(default=512, ge=128, le=4096)
    cpus: float = Field(default=1.0, ge=0.25, le=4.0)
    pids_limit: int = Field(default=256, ge=32, le=2048)
    ai_enabled: bool = True
    ai_provider: AIProviderName = "heuristic"
    repository_roots: list[str] = Field(default_factory=list, max_length=20)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("repository_roots")
    @classmethod
    def _validate_roots(cls, roots: list[str]) -> list[str]:
        return sorted({str(validate_allowed_root(r)) for r in roots})


class SettingsService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def get(self) -> RuntimeConfig:
        with self.db.session() as session:
            row = session.get(AppSetting, _KEY)
            if row is None:
                return RuntimeConfig(log_level=self.settings.log_level)
            # Stored roots were validated when saved; re-validate in case they vanished.
            data = dict(row.value)
            data["repository_roots"] = [
                r for r in data.get("repository_roots", []) if Path(r).is_dir()
            ]
            return RuntimeConfig.model_validate(data)

    def update(self, config: RuntimeConfig) -> RuntimeConfig:
        with self.db.transaction() as session:
            row = session.get(AppSetting, _KEY)
            if row is None:
                session.add(AppSetting(key=_KEY, value=config.model_dump()))
            else:
                row.value = config.model_dump()
        return config

    def allowed_roots(self) -> list[Path]:
        roots = [self.settings.managed_repos_dir, *self.settings.allowed_roots]
        roots += [Path(r) for r in self.get().repository_roots]
        return roots
