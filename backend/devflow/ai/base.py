"""AI provider interface.

A provider receives a fully prepared, redacted context (failures plus the few source
files they reference) and returns a diagnosis and an optional unified diff. Providers
never touch the repository: any patch they return goes through validation, a dry-run
``git apply --check``, and explicit human approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from devflow.runners.base import Availability


@dataclass(frozen=True)
class FailureContext:
    nodeid: str | None
    category: str
    summary: str
    exception_type: str | None
    file: str | None
    line: int | None
    traceback: str | None


@dataclass
class AnalysisInput:
    run_id: int
    repository_name: str
    is_example: bool
    failures: list[FailureContext]
    files: dict[str, str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisOutput:
    root_cause: str
    explanation: str
    confidence: str = "low"
    patch: str | None = None
    patch_rationale: str | None = None


class ProviderError(Exception):
    pass


class Provider(Protocol):
    name: str
    label: str
    is_ai: bool
    generates_patches: bool

    def availability(self) -> Availability: ...

    def analyze(self, request: AnalysisInput, workspace: Path) -> AnalysisOutput: ...
