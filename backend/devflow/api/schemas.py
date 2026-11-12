"""Request and response models for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class RepositoryCreate(BaseModel):
    path: str = Field(
        min_length=1, max_length=1024, description="Absolute path inside an approved directory"
    )
    name: str | None = Field(default=None, max_length=120)


class RepositoryOut(ORM):
    id: int
    name: str
    path: str
    is_example: bool
    languages: dict[str, Any]
    structure: dict[str, Any]
    created_at: datetime
    last_run_at: datetime | None


class CommitOut(BaseModel):
    sha: str
    author: str
    committed_at: datetime
    message: str


class RepositoryDetail(RepositoryOut):
    branch: str | None
    head: str | None
    branches: list[str]
    recent_commits: list[CommitOut]


class RunCreate(BaseModel):
    repository_id: int
    revision: str = Field(default="HEAD", min_length=1, max_length=200)


class RevisionOut(ORM):
    sha: str
    ref: str | None
    message: str
    author: str
    committed_at: datetime | None


class RunOut(ORM):
    id: int
    repository_id: int
    repository_name: str | None = None
    kind: str
    parent_run_id: int | None
    patch_id: int | None
    status: str
    runner: str
    sandboxed: bool
    trigger: str
    correlation_id: str | None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    test_duration_ms: int | None
    overhead_ms: int | None
    exit_code: int | None
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    error_message: str | None
    source_info: dict[str, Any]
    revision: RevisionOut | None


class RunPage(BaseModel):
    items: list[RunOut]
    total: int
    limit: int
    offset: int


class TestResultOut(ORM):
    id: int
    nodeid: str
    file: str | None
    name: str
    outcome: str
    duration_ms: float
    message: str | None
    traceback: str | None


class FailureOut(BaseModel):
    id: int
    run_id: int
    category: str
    exception_type: str | None
    signature: str
    nodeid: str | None
    summary: str
    file: str | None
    line: int | None
    created_at: datetime
    resolved_by_run_id: int | None
    resolved_at: datetime | None
    occurrences: int
    traceback: str | None


class LogOut(BaseModel):
    run_id: int
    stream: Literal["stdout", "stderr"]
    content: str


class AnalysisCreate(BaseModel):
    run_id: int
    provider: Literal["heuristic", "claude-code", "codex", "demo"] | None = None


class DecisionOut(ORM):
    id: int
    decision: str
    actor: str
    comment: str | None
    created_at: datetime


class PatchOut(ORM):
    id: int
    analysis_id: int
    run_id: int
    status: str
    diff: str
    files: list[str]
    rationale: str | None
    risk_level: str
    risk_score: int
    risk_reasons: list[str]
    validation_errors: list[str]
    lines_added: int
    lines_removed: int
    retest_run_id: int | None
    verification_run_id: int | None
    attempt: int
    exported_branch: str | None
    exported_commit: str | None
    created_at: datetime
    decided_at: datetime | None
    decisions: list[DecisionOut]
    provider: str | None = None
    is_ai: bool | None = None


class AnalysisOut(ORM):
    id: int
    run_id: int
    provider: str
    is_ai: bool
    status: str
    created_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    root_cause: str | None
    explanation: str | None
    confidence: str | None
    error: str | None
    warnings: list[str]
    context_files: list[str]
    mode: str
    attempts: list[dict[str, Any]]
    patch_ids: list[int] = []


class ReviewCreate(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)


class ExportCreate(BaseModel):
    confirm: bool = Field(
        description="Must be true: exporting writes a new branch to your repository"
    )
    branch: str | None = Field(
        default=None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$"
    )


class ProviderOut(BaseModel):
    name: str
    label: str
    is_ai: bool
    generates_patches: bool
    available: bool
    reason: str | None
    version: str | None
    default: bool
