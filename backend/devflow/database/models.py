"""SQLAlchemy ORM models. See docs/ARCHITECTURE.md for the schema diagram."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC and always returns timezone-aware datetimes (SQLite drops tzinfo)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self not in (RunStatus.QUEUED, RunStatus.RUNNING)


ACTIVE_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)


class RunKind(enum.StrEnum):
    REVISION = "revision"
    PATCH_RETEST = "patch_retest"


class FailureCategory(enum.StrEnum):
    ASSERTION = "assertion"
    EXCEPTION = "exception"
    SYNTAX_ERROR = "syntax_error"
    MISSING_DEPENDENCY = "missing_dependency"
    TIMEOUT = "timeout"
    COLLECTION_ERROR = "collection_error"
    INFRASTRUCTURE = "infrastructure"


class AnalysisStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class PatchStatus(enum.StrEnum):
    PROPOSED = "proposed"
    INVALID = "invalid"
    REJECTED = "rejected"
    APPROVED = "approved"
    RETESTING = "retesting"
    RETEST_PASSED = "retest_passed"
    RETEST_FAILED = "retest_failed"
    EXPORTED = "exported"


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    is_example: Mapped[bool] = mapped_column(Boolean, default=False)
    languages: Mapped[dict[str, Any]] = mapped_column(default=dict)
    structure: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    revisions: Mapped[list[Revision]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    runs: Mapped[list[TestRun]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class Revision(Base):
    __tablename__ = "revisions"
    __table_args__ = (UniqueConstraint("repository_id", "sha"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    sha: Mapped[str] = mapped_column(String(40))
    ref: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(String(500), default="")
    author: Mapped[str] = mapped_column(String(255), default="")
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    repository: Mapped[Repository] = relationship(back_populates="revisions")


class TestRun(Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "test_runs"
    __table_args__ = (Index("ix_test_runs_repo_created", "repository_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id"))
    kind: Mapped[str] = mapped_column(String(20), default=RunKind.REVISION.value)
    parent_run_id: Mapped[int | None] = mapped_column(ForeignKey("test_runs.id"))
    patch_id: Mapped[int | None] = mapped_column(
        ForeignKey("patch_proposals.id", use_alter=True, name="fk_test_runs_patch_id")
    )
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED.value, index=True)
    # Holds "<repo>:<sha>:<kind>:<patch>" while the run is queued/running and NULL afterwards.
    # The UNIQUE constraint makes duplicate submissions fail atomically, even across threads.
    active_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    runner: Mapped[str] = mapped_column(String(20))
    sandboxed: Mapped[bool] = mapped_column(Boolean)
    trigger: Mapped[str] = mapped_column(String(20), default="api")
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    test_duration_ms: Mapped[int | None] = mapped_column(Integer)
    overhead_ms: Mapped[int | None] = mapped_column(Integer)

    exit_code: Mapped[int | None] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    # Logs are stored as files under the run directory, not in the database.
    stdout_path: Mapped[str | None] = mapped_column(String(1024))
    stderr_path: Mapped[str | None] = mapped_column(String(1024))
    source_info: Mapped[dict[str, Any]] = mapped_column(default=dict)

    repository: Mapped[Repository] = relationship(back_populates="runs")
    revision: Mapped[Revision | None] = relationship()
    results: Mapped[list[TestResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="TestResult.id"
    )
    failures: Mapped[list[Failure]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Failure.id",
        foreign_keys="Failure.run_id",
    )


class TestResult(Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "test_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    nodeid: Mapped[str] = mapped_column(String(1000))
    file: Mapped[str | None] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(500))
    outcome: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)

    run: Mapped[TestRun] = relationship(back_populates="results")


class Failure(Base):
    __tablename__ = "failures"
    __table_args__ = (Index("ix_failures_signature", "signature"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    test_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_results.id", ondelete="CASCADE")
    )
    category: Mapped[str] = mapped_column(String(30))
    exception_type: Mapped[str | None] = mapped_column(String(200))
    signature: Mapped[str] = mapped_column(String(32))
    nodeid: Mapped[str | None] = mapped_column(String(1000))
    summary: Mapped[str] = mapped_column(Text)
    file: Mapped[str | None] = mapped_column(String(500))
    line: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    resolved_by_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    run: Mapped[TestRun] = relationship(back_populates="failures", foreign_keys=[run_id])


class AnalysisRequest(Base):
    __tablename__ = "analysis_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    is_ai: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), default=AnalysisStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    root_cause: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    # Prompt-injection indicators found in the repository content sent to the provider.
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    context_files: Mapped[list[Any]] = mapped_column(default=list)

    patches: Mapped[list[PatchProposal]] = relationship(back_populates="analysis")


class PatchProposal(Base):
    __tablename__ = "patch_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_requests.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=PatchStatus.PROPOSED.value)
    diff: Mapped[str] = mapped_column(Text)
    files: Mapped[list[Any]] = mapped_column(default=list)
    rationale: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(10))
    risk_score: Mapped[int] = mapped_column(Integer)
    risk_reasons: Mapped[list[Any]] = mapped_column(default=list)
    validation_errors: Mapped[list[Any]] = mapped_column(default=list)
    lines_added: Mapped[int] = mapped_column(Integer, default=0)
    lines_removed: Mapped[int] = mapped_column(Integer, default=0)
    retest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL")
    )
    exported_branch: Mapped[str | None] = mapped_column(String(255))
    exported_commit: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    analysis: Mapped[AnalysisRequest] = relationship(back_populates="patches")
    decisions: Mapped[list[ApprovalDecision]] = relationship(
        back_populates="patch", cascade="all, delete-orphan", order_by="ApprovalDecision.id"
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(
        ForeignKey("patch_proposals.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(100), default="local-user")
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    patch: Mapped[PatchProposal] = relationship(back_populates="decisions")


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(64), index=True)
    scenario: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(40))
    iteration: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float)
    success: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
