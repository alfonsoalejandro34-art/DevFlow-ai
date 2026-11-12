"""Test-run lifecycle: submission, execution, result parsing, cancellation, comparison."""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from devflow.config import Settings
from devflow.database.models import (
    ACTIVE_STATUSES,
    Failure,
    PatchProposal,
    PatchStatus,
    Repository,
    RunKind,
    RunStatus,
    TestResult,
    TestRun,
    utcnow,
)
from devflow.database.session import Database
from devflow.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed
from devflow.observability import metrics
from devflow.observability.logging import correlation_id
from devflow.observability.tracing import tracer
from devflow.runners.base import Runner, RunOutcome, RunSpec
from devflow.security.paths import make_private_dir
from devflow.security.redact import redact
from devflow.services import git
from devflow.services.classify import classify, classify_run_error, signature
from devflow.services.junit import JUnitParseError, ParsedTest, parse_junit, suite_duration_ms
from devflow.services.repos import RepositoryService
from devflow.services.settings import SettingsService
from devflow.workers.jobs import JobManager

log = logging.getLogger(__name__)

# Prepares the source tree for a run inside the given directory and returns metadata.
SourcePreparer = Callable[[Path], dict[str, Any]]
MAX_LOG_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True)
class RunFilters:
    repository_id: int | None = None
    status: str | None = None
    query: str | None = None
    limit: int = 50
    offset: int = 0


class RunService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        runtime: SettingsService,
        repos: RepositoryService,
        runner: Runner,
        jobs: JobManager,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.repos = repos
        self.runner = runner
        self.jobs = jobs

    # ---- submission -----------------------------------------------------------------

    def submit(self, repo_id: int, rev: str = "HEAD", trigger: str = "api") -> TestRun:
        repo = self.repos.get(repo_id)
        if not repo.structure.get("supported", False):
            raise ValidationFailed(
                f"Repository is not supported: {repo.structure.get('unsupported_reason')}"
            )
        revision = self.repos.ensure_revision(repo, rev)
        repo_path = self.repos.path_of(repo)
        sha = revision.sha

        def prepare(dest: Path) -> dict[str, Any]:
            info = git.snapshot(repo_path, sha, dest)
            return {"files": info.files, "bytes": info.bytes, "skipped": info.skipped[:50]}

        return self._create_and_enqueue(
            repo=repo,
            revision_id=revision.id,
            kind=RunKind.REVISION,
            active_key=f"{repo.id}:{sha}:revision",
            trigger=trigger,
            prepare=prepare,
            source_info={"sha": sha, "ref": rev},
        )

    def submit_patch_retest(
        self, repo: Repository, base_run: TestRun, patch_id: int, prepare: SourcePreparer
    ) -> TestRun:
        return self._create_and_enqueue(
            repo=repo,
            revision_id=base_run.revision_id,
            kind=RunKind.PATCH_RETEST,
            active_key=f"{repo.id}:patch:{patch_id}",
            trigger="patch-approval",
            prepare=prepare,
            source_info={"sha": base_run.source_info.get("sha"), "patch_id": patch_id},
            parent_run_id=base_run.id,
            patch_id=patch_id,
        )

    def _create_and_enqueue(
        self,
        *,
        repo: Repository,
        revision_id: int | None,
        kind: RunKind,
        active_key: str,
        trigger: str,
        prepare: SourcePreparer,
        source_info: dict[str, Any],
        parent_run_id: int | None = None,
        patch_id: int | None = None,
    ) -> TestRun:
        availability = self.runner.availability()
        if not availability.available:
            raise ServiceUnavailable(f"Test runner unavailable: {availability.reason}")
        run = TestRun(
            repository_id=repo.id,
            revision_id=revision_id,
            kind=kind.value,
            parent_run_id=parent_run_id,
            patch_id=patch_id,
            active_key=active_key,
            runner=self.runner.name,
            sandboxed=self.runner.sandboxed,
            trigger=trigger,
            correlation_id=correlation_id.get(),
            source_info=source_info,
        )
        try:
            with self.db.transaction() as session:
                session.add(run)
                session.execute(
                    update(Repository).where(Repository.id == repo.id).values(last_run_at=utcnow())
                )
        except IntegrityError as exc:
            raise Conflict("An identical test run is already queued or running") from exc
        log.info(
            "run queued", extra={"run_id": run.id, "repository_id": repo.id, "kind": kind.value}
        )
        run_id = run.id
        self.jobs.submit(f"run:{run_id}", lambda cancel: self._execute(run_id, prepare, cancel))
        return run

    # ---- execution ------------------------------------------------------------------

    def _execute(self, run_id: int, prepare: SourcePreparer, cancel: threading.Event) -> None:
        run_dir = self.settings.runs_dir / str(run_id)
        # SQLite can reuse the id of a deleted run; never let stale artifacts leak in.
        shutil.rmtree(run_dir, ignore_errors=True)
        make_private_dir(run_dir)
        source_dir, output_dir = run_dir / "src", make_private_dir(run_dir / "out")
        with tracer.start_as_current_span("devflow.run", attributes={"run_id": run_id}) as span:
            with self.db.transaction() as session:
                run = session.get(TestRun, run_id)
                assert run is not None
                if run.cancel_requested or cancel.is_set():
                    self._finish(session, run, RunStatus.CANCELLED, "Cancelled before start")
                    return
                run.status = RunStatus.RUNNING.value
                run.started_at = utcnow()
            config = self.runtime.get()
            try:
                with tracer.start_as_current_span("devflow.prepare_source"):
                    info = prepare(source_dir)
                with tracer.start_as_current_span(
                    "devflow.runner.execute", attributes={"runner": self.runner.name}
                ):
                    spec = RunSpec(
                        run_id=run_id,
                        source_dir=source_dir,
                        output_dir=output_dir,
                        timeout_seconds=config.test_timeout_seconds,
                        memory_mb=config.memory_mb,
                        cpus=config.cpus,
                        pids_limit=config.pids_limit,
                    )
                    outcome = self.runner.run(spec, cancel)
            except Exception as exc:
                log.exception("run failed before tests completed", extra={"run_id": run_id})
                metrics.JOB_ERRORS.inc()
                with self.db.transaction() as session:
                    run = session.get(TestRun, run_id)
                    assert run is not None
                    self._finish(
                        session,
                        run,
                        RunStatus.ERROR,
                        f"{type(exc).__name__}: {redact(str(exc))[:1000]}",
                    )
                return
            with tracer.start_as_current_span("devflow.parse_results"):
                status = self._record_outcome(run_id, spec, outcome, info)
            span.set_attribute("status", status.value)

    def _record_outcome(
        self, run_id: int, spec: RunSpec, outcome: RunOutcome, source_info: dict[str, Any]
    ) -> RunStatus:
        _redact_file(spec.stdout_path)
        _redact_file(spec.stderr_path)
        parsed: list[ParsedTest] = []
        parse_error: str | None = None
        if spec.junit_path.exists():
            try:
                parsed = parse_junit(spec.junit_path)
            except JUnitParseError as exc:
                parse_error = str(exc)
        status, message = _decide_status(outcome, parsed, parse_error)

        with self.db.transaction() as session:
            run = session.get(TestRun, run_id)
            assert run is not None
            run.exit_code = outcome.exit_code
            run.stdout_path = str(spec.stdout_path)
            run.stderr_path = str(spec.stderr_path)
            run.source_info = {**run.source_info, "snapshot": source_info}
            test_ms = suite_duration_ms(spec.junit_path) if parsed else None
            run.test_duration_ms = test_ms
            run.overhead_ms = max(0, outcome.wall_ms - test_ms) if test_ms is not None else None

            for test in parsed:
                result = TestResult(
                    run_id=run.id,
                    nodeid=test.nodeid[:1000],
                    file=test.file,
                    name=test.name[:500],
                    outcome=test.outcome,
                    duration_ms=test.duration_ms,
                    message=test.message,
                    traceback=test.traceback,
                )
                session.add(result)
                metrics.TESTS_TOTAL.labels(test.outcome).inc()
                if test.outcome in ("failed", "error"):
                    session.flush()
                    c = classify(test.outcome, test.message, test.traceback, test.nodeid)
                    session.add(
                        Failure(
                            run_id=run.id,
                            repository_id=run.repository_id,
                            test_result_id=result.id,
                            category=c.category.value,
                            exception_type=c.exception_type,
                            signature=signature(
                                c.category.value, test.nodeid, c.exception_type, c.summary
                            ),
                            nodeid=test.nodeid[:1000],
                            summary=c.summary,
                            file=c.file,
                            line=c.line,
                        )
                    )
            if not parsed and status in (RunStatus.ERROR, RunStatus.FAILED, RunStatus.TIMEOUT):
                stdout = _read_tail(spec.stdout_path, 20_000)
                stderr = _read_tail(spec.stderr_path, 20_000)
                c = classify_run_error(stdout, stderr, outcome.exit_code)
                if outcome.timed_out:
                    c.summary = f"Run exceeded the {spec.timeout_seconds}s timeout"
                session.add(
                    Failure(
                        run_id=run.id,
                        repository_id=run.repository_id,
                        category="timeout" if outcome.timed_out else c.category.value,
                        exception_type=c.exception_type,
                        signature=signature(c.category.value, None, c.exception_type, c.summary),
                        summary=c.summary,
                        file=c.file,
                        line=c.line,
                    )
                )

            run.total = len(parsed)
            run.passed = sum(t.outcome == "passed" for t in parsed)
            run.failed = sum(t.outcome == "failed" for t in parsed)
            run.errors = sum(t.outcome == "error" for t in parsed)
            run.skipped = sum(t.outcome == "skipped" for t in parsed)
            self._finish(session, run, status, message)
            session.flush()
            self._resolve_previous_failures(session, run, parsed)
        return status

    def _finish(self, session: Any, run: TestRun, status: RunStatus, message: str | None) -> None:
        run.status = status.value
        run.error_message = message
        run.active_key = None
        run.finished_at = utcnow()
        run.duration_ms = int((run.finished_at - run.created_at).total_seconds() * 1000)
        if run.patch_id is not None:
            _record_retest_verdict(session, run.patch_id, status)
        metrics.RUNS_TOTAL.labels(status.value, run.runner).inc()
        metrics.RUN_DURATION.labels(run.runner).observe(run.duration_ms / 1000)
        if run.overhead_ms is not None:
            metrics.RUNNER_OVERHEAD.labels(run.runner).observe(run.overhead_ms / 1000)
        if status == RunStatus.ERROR:
            log.warning("run ended with error", extra={"run_id": run.id, "error": message})
        else:
            log.info("run finished", extra={"run_id": run.id, "status": status.value})

    def _resolve_previous_failures(
        self, session: Any, run: TestRun, parsed: list[ParsedTest]
    ) -> None:
        passed = {t.nodeid for t in parsed if t.outcome == "passed"}
        if not passed:
            return
        open_failures = session.scalars(
            select(Failure).where(
                Failure.repository_id == run.repository_id,
                Failure.resolved_by_run_id.is_(None),
                Failure.run_id != run.id,
                Failure.nodeid.in_(passed),
            )
        )
        for failure in open_failures:
            failure.resolved_by_run_id = run.id
            failure.resolved_at = run.finished_at

    # ---- control --------------------------------------------------------------------

    def cancel(self, run_id: int) -> TestRun:
        with self.db.transaction() as session:
            run = session.get(TestRun, run_id)
            if run is None:
                raise NotFound(f"Run {run_id} not found")
            if run.status not in ACTIVE_STATUSES:
                raise Conflict(f"Run {run_id} is already {run.status}")
            run.cancel_requested = True
        signalled = self.jobs.cancel(f"run:{run_id}")
        if not signalled:
            # No live job (e.g. lost on restart): finalize directly.
            with self.db.transaction() as session:
                run = session.get(TestRun, run_id)
                assert run is not None
                if run.status in ACTIVE_STATUSES:
                    self._finish(session, run, RunStatus.CANCELLED, "Cancelled")
        log.info("run cancellation requested", extra={"run_id": run_id})
        return self.get(run_id)

    def recover_interrupted(self) -> int:
        """Mark runs left queued/running by a previous process as errors."""
        with self.db.transaction() as session:
            stale = list(
                session.scalars(select(TestRun).where(TestRun.status.in_(ACTIVE_STATUSES)))
            )
            for run in stale:
                self._finish(
                    session,
                    run,
                    RunStatus.ERROR,
                    "Interrupted: DevFlow restarted while the run was active",
                )
        return len(stale)

    # ---- queries --------------------------------------------------------------------

    def get(self, run_id: int) -> TestRun:
        with self.db.session() as session:
            run = session.get(TestRun, run_id)
            if run is None:
                raise NotFound(f"Run {run_id} not found")
            _ = run.repository, run.revision
            return run

    def search(self, filters: RunFilters) -> tuple[list[TestRun], int]:
        stmt = select(TestRun).join(Repository)
        if filters.repository_id is not None:
            stmt = stmt.where(TestRun.repository_id == filters.repository_id)
        if filters.status:
            stmt = stmt.where(TestRun.status == filters.status)
        if filters.query:
            q = f"%{filters.query.strip()[:100]}%"
            stmt = stmt.where(
                or_(
                    Repository.name.ilike(q),
                    TestRun.source_info["sha"].as_string().ilike(q),
                    TestRun.error_message.ilike(q),
                )
            )
        with self.db.session() as session:
            total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = session.scalars(
                stmt.order_by(TestRun.created_at.desc(), TestRun.id.desc())
                .limit(filters.limit)
                .offset(filters.offset)
            )
            runs = list(rows)
            for run in runs:
                _ = run.repository, run.revision
            return runs, total

    def results(self, run_id: int) -> list[TestResult]:
        self.get(run_id)
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(TestResult).where(TestResult.run_id == run_id).order_by(TestResult.id)
                )
            )

    def failures(self, run_id: int) -> list[dict[str, Any]]:
        self.get(run_id)
        with self.db.session() as session:
            failures = list(
                session.scalars(
                    select(Failure).where(Failure.run_id == run_id).order_by(Failure.id)
                )
            )
            counts = dict(
                session.execute(
                    select(Failure.signature, func.count(func.distinct(Failure.run_id)))
                    .where(Failure.signature.in_([f.signature for f in failures]))
                    .group_by(Failure.signature)
                ).all()
            )
            tracebacks = {
                r.id: r.traceback
                for r in session.scalars(
                    select(TestResult).where(
                        TestResult.id.in_([f.test_result_id for f in failures if f.test_result_id])
                    )
                )
            }
        return [
            {
                **{
                    c: getattr(f, c)
                    for c in (
                        "id",
                        "run_id",
                        "category",
                        "exception_type",
                        "signature",
                        "nodeid",
                        "summary",
                        "file",
                        "line",
                        "created_at",
                        "resolved_by_run_id",
                        "resolved_at",
                    )
                },
                "occurrences": counts.get(f.signature, 1),
                "traceback": tracebacks.get(f.test_result_id) if f.test_result_id else None,
            }
            for f in failures
        ]

    def logs(self, run_id: int, stream: str) -> str:
        run = self.get(run_id)
        if stream not in ("stdout", "stderr"):
            raise ValidationFailed("stream must be 'stdout' or 'stderr'")
        raw = run.stdout_path if stream == "stdout" else run.stderr_path
        if not raw:
            return ""
        path = Path(raw).resolve()
        # Only ever serve files from DevFlow's own run directory.
        if not path.is_relative_to(self.settings.runs_dir.resolve()):
            raise ValidationFailed("Log path is outside the runs directory")
        return _read_tail(path, MAX_LOG_RESPONSE_BYTES)

    def compare(self, run_id: int, other_id: int | None = None) -> dict[str, Any]:
        run = self.get(run_id)
        if other_id is None:
            other_id = run.parent_run_id or self._previous_run_id(run)
        if other_id is None:
            raise NotFound("No earlier run to compare with")
        other = self.get(other_id)
        if other.repository_id != run.repository_id:
            raise ValidationFailed("Runs belong to different repositories")
        before = {r.nodeid: r.outcome for r in self.results(other.id)}
        after = {r.nodeid: r.outcome for r in self.results(run.id)}
        bad = ("failed", "error")
        return {
            "run_id": run.id,
            "baseline_run_id": other.id,
            "fixed": sorted(n for n, o in after.items() if o == "passed" and before.get(n) in bad),
            "regressed": sorted(
                n for n, o in after.items() if o in bad and before.get(n) == "passed"
            ),
            "still_failing": sorted(
                n for n, o in after.items() if o in bad and before.get(n) in bad
            ),
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
            "baseline": _summary(other),
            "current": _summary(run),
        }

    def _previous_run_id(self, run: TestRun) -> int | None:
        with self.db.session() as session:
            return session.scalar(
                select(TestRun.id)
                .where(
                    TestRun.repository_id == run.repository_id,
                    TestRun.id < run.id,
                    TestRun.status.notin_(ACTIVE_STATUSES),
                )
                .order_by(TestRun.id.desc())
                .limit(1)
            )


def _record_retest_verdict(session: Any, patch_id: int, status: RunStatus) -> None:
    """Advance the patch in the same transaction that finishes its retest run."""
    patch = session.get(PatchProposal, patch_id)
    if patch is None or patch.status != PatchStatus.RETESTING.value:
        return
    if status == RunStatus.PASSED:
        patch.status = PatchStatus.RETEST_PASSED.value
    elif status in (RunStatus.ERROR, RunStatus.CANCELLED):
        patch.status = PatchStatus.APPROVED.value  # no verdict; the retest can be rerun
    else:
        patch.status = PatchStatus.RETEST_FAILED.value


def _decide_status(
    outcome: RunOutcome, parsed: list[ParsedTest], parse_error: str | None
) -> tuple[RunStatus, str | None]:
    if outcome.infra_error:
        return RunStatus.ERROR, outcome.infra_error
    if outcome.cancelled:
        return RunStatus.CANCELLED, "Cancelled by user"
    if outcome.timed_out:
        return RunStatus.TIMEOUT, "Run exceeded its wall-clock timeout and was terminated"
    if outcome.output_limit_exceeded:
        return RunStatus.ERROR, "Test output exceeded the log size limit; run terminated"
    if outcome.exit_code == 5:
        return RunStatus.ERROR, "pytest collected no tests"
    if not parsed:
        if outcome.exit_code == 2:
            return RunStatus.FAILED, "Test collection failed"
        reason = parse_error or "no JUnit report produced"
        return RunStatus.ERROR, f"pytest exited with code {outcome.exit_code}: {reason}"
    if outcome.exit_code == 0 and all(t.outcome in ("passed", "skipped") for t in parsed):
        return RunStatus.PASSED, None
    if outcome.exit_code in (0, 1, 2):
        return RunStatus.FAILED, None
    return RunStatus.ERROR, f"pytest exited with code {outcome.exit_code}"


def _summary(run: TestRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "passed": run.passed,
        "failed": run.failed + run.errors,
        "total": run.total,
        "duration_ms": run.duration_ms,
    }


def _read_tail(path: Path, max_bytes: int) -> str:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except FileNotFoundError:
        return ""
    prefix = "[... truncated ...]\n" if size > max_bytes else ""
    return prefix + data.decode("utf-8", "replace")


def _redact_file(path: Path) -> None:
    if path.exists():
        text = path.read_text(errors="replace")
        redacted = redact(text)
        if redacted != text:
            path.write_text(redacted)
