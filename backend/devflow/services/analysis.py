"""Failure analysis requests and the patch review workflow.

Workflow: analysis -> patch proposal (validated + dry-run applied) -> human decision ->
on approval, the patch is applied to a fresh snapshot in DevFlow's own run directory
and retested. The user's repository is only changed by ``export_patch``, which requires
an explicit confirmation and writes a new branch without touching the working tree.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from devflow.ai.base import AnalysisInput, AnalysisOutput, FailureContext, Provider, ProviderError
from devflow.ai.context import load_files, scan_for_injection, select_files
from devflow.config import Settings
from devflow.database.models import (
    AnalysisRequest,
    AnalysisStatus,
    ApprovalDecision,
    Failure,
    PatchProposal,
    PatchStatus,
    Repository,
    RunStatus,
    TestResult,
    TestRun,
    utcnow,
)
from devflow.database.session import Database
from devflow.errors import Conflict, DevFlowError, NotFound, ValidationFailed
from devflow.observability import metrics
from devflow.observability.tracing import tracer
from devflow.security.patches import normalize_diff, validate_patch
from devflow.security.paths import make_private_dir, resolve_within
from devflow.security.redact import redact
from devflow.services import git
from devflow.services.agent_workspace import diff_workspace, prepare_workspace
from devflow.services.patching import apply_patch
from devflow.services.repos import RepositoryService
from devflow.services.runs import RunService, SourcePreparer
from devflow.services.settings import SettingsService
from devflow.workers.jobs import JobManager

log = logging.getLogger(__name__)
MAX_FAILURES_IN_CONTEXT = 8
MAX_FEEDBACK_FAILURES = 5
MAX_FEEDBACK_TRACEBACK = 1500
MAX_FEEDBACK_DIFF = 8000


@dataclass
class _Candidate:
    """The latest patch produced by the self-correcting loop."""

    attempt: int
    diff: str
    rationale: str | None
    errors: list[str]
    verification_run_id: int | None = None


def _rejection_feedback(diff: str, errors: list[str]) -> str:
    lines = ["DevFlow rejected your change before running tests:"]
    lines += [f"- {e}" for e in errors[:10]]
    if diff:
        lines += ["", "Your previous change was:", "```diff", diff[:MAX_FEEDBACK_DIFF], "```"]
    return "\n".join(lines)


_ANALYZABLE = (RunStatus.FAILED.value, RunStatus.ERROR.value, RunStatus.TIMEOUT.value)


class AnalysisService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        runtime: SettingsService,
        repos: RepositoryService,
        runs: RunService,
        providers: dict[str, Provider],
        jobs: JobManager,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.repos = repos
        self.runs = runs
        self.providers = providers
        self.jobs = jobs

    # ---- providers ------------------------------------------------------------------

    def provider_status(self) -> list[dict[str, Any]]:
        config = self.runtime.get()
        out = []
        for provider in self.providers.values():
            availability = provider.availability()
            enabled = not provider.is_ai or config.ai_enabled
            out.append(
                {
                    "name": provider.name,
                    "label": provider.label,
                    "is_ai": provider.is_ai,
                    "generates_patches": provider.generates_patches,
                    "available": availability.available and enabled,
                    "reason": availability.reason
                    if enabled
                    else "AI integration is disabled in Settings",
                    "version": availability.version,
                    "default": provider.name == config.ai_provider,
                }
            )
        return out

    # ---- analysis -------------------------------------------------------------------

    def request(self, run_id: int, provider_name: str | None = None) -> AnalysisRequest:
        run = self.runs.get(run_id)
        if run.status not in _ANALYZABLE:
            raise Conflict(f"Run {run_id} is {run.status}; only failed runs can be analyzed")
        config = self.runtime.get()
        name = provider_name or config.ai_provider
        provider = self.providers.get(name)
        if provider is None:
            raise ValidationFailed(f"Unknown provider {name!r}")
        agent = config.ai_mode == "agent" and provider.supports_agent_mode
        record = AnalysisRequest(
            run_id=run_id, provider=name, is_ai=provider.is_ai, mode="agent" if agent else "text"
        )
        reason = None
        if provider.is_ai and not config.ai_enabled:
            reason = "AI integration is disabled in Settings"
        else:
            availability = provider.availability()
            if not availability.available:
                reason = availability.reason or "provider unavailable"
        if reason:
            record.status = AnalysisStatus.UNAVAILABLE.value
            record.error = f"{provider.label} is unavailable: {reason}. The rule-based 'heuristic' provider works without AI."
            record.completed_at = utcnow()
        with self.db.transaction() as session:
            session.add(record)
        if reason:
            metrics.ANALYSES_TOTAL.labels(name, "unavailable").inc()
            return record
        analysis_id = record.id
        self.jobs.submit(
            f"analysis:{analysis_id}", lambda cancel: self._execute(analysis_id, cancel)
        )
        return record

    def _execute(self, analysis_id: int, cancel: threading.Event) -> None:
        started = time.monotonic()
        with self.db.transaction() as session:
            record = session.get(AnalysisRequest, analysis_id)
            assert record is not None
            record.status = AnalysisStatus.RUNNING.value
            run = session.get(TestRun, record.run_id)
            assert run is not None
            repo = session.get(Repository, run.repository_id)
            assert repo is not None
            provider = self.providers[record.provider]
            agent = record.mode == "agent"
        config = self.runtime.get()
        max_attempts = config.ai_max_attempts if provider.accepts_feedback else 1
        verify = config.ai_verify_patches and provider.generates_patches
        source_dir = self.settings.runs_dir / str(run.id) / "src"
        root = make_private_dir(self.settings.ai_workspaces_dir / str(analysis_id))
        attempts: list[dict[str, Any]] = []
        output: AnalysisOutput | None = None
        candidate: _Candidate | None = None
        error: str | None = None
        with tracer.start_as_current_span(
            "devflow.analysis",
            attributes={"analysis_id": analysis_id, "provider": provider.name, "agent": agent},
        ):
            try:
                request = self._build_input(run, repo)
                feedback: str | None = None
                for number in range(1, max_attempts + 1):
                    if cancel.is_set():
                        break
                    attempt_started = time.monotonic()
                    attempt: dict[str, Any] = {"attempt": number}
                    workspace = root / f"attempt-{number}"
                    try:
                        if agent:
                            excluded = prepare_workspace(source_dir, workspace)
                            if excluded:
                                attempt["excluded_files"] = excluded
                        else:
                            self._write_context(workspace, request.files)
                        output = provider.analyze(
                            request, workspace, agent=agent, feedback=feedback
                        )
                    except ProviderError as exc:
                        attempt.update(outcome="provider_error", errors=[redact(str(exc))[:500]])
                        attempts.append(attempt)
                        if candidate is None:
                            error = str(exc)
                        break
                    finally:
                        # Agent workspaces are kept until their diff has been computed.
                        if not agent:
                            shutil.rmtree(workspace, ignore_errors=True)

                    if agent:
                        changes = diff_workspace(source_dir, workspace)
                        shutil.rmtree(workspace, ignore_errors=True)
                        diff, errors = changes.diff, list(changes.problems)
                    else:
                        diff, errors = output.patch or "", []
                    if not diff and not errors:
                        attempt.update(
                            outcome="no_change",
                            detail="The provider made no changes" if agent else "No patch proposed",
                        )
                        attempts.append(attempt | self._elapsed(attempt_started))
                        break
                    if diff:
                        errors += self._check_patch(source_dir, diff)
                    candidate = _Candidate(number, diff, output.patch_rationale, errors)
                    attempt["files"] = validate_patch(diff).files if diff else []
                    done = False
                    if errors:
                        attempt.update(outcome="invalid", errors=errors[:10])
                        feedback = _rejection_feedback(diff, errors)
                    elif verify:
                        check = self.runs.run_verification(
                            repo,
                            run,
                            self._patched_source(repo, run, diff),
                            label=f"{analysis_id}:{number}",
                        )
                        candidate.verification_run_id = check.id
                        attempt.update(
                            verification_run_id=check.id,
                            passed=check.passed,
                            failed=check.failed + check.errors,
                        )
                        if check.status == RunStatus.PASSED.value:
                            attempt["outcome"] = "verified"
                            done = True
                        else:
                            attempt["outcome"] = (
                                "tests_failed"
                                if check.status == RunStatus.FAILED.value
                                else f"verification_{check.status}"
                            )
                            feedback = self._test_feedback(diff, check)
                    else:
                        attempt["outcome"] = "valid"
                        done = True
                    attempts.append(attempt | self._elapsed(attempt_started))
                    self._save_progress(analysis_id, attempts)
                    if done:
                        break
            except (ValidationFailed, OSError) as exc:
                error = str(exc)
            except Exception as exc:
                log.exception("analysis crashed", extra={"analysis_id": analysis_id})
                error = f"{type(exc).__name__}: {exc}"
            finally:
                shutil.rmtree(root, ignore_errors=True)

        if error is not None or output is None:
            self._fail(analysis_id, started, error or "Analysis was cancelled", attempts)
            return
        with self.db.transaction() as session:
            record = session.get(AnalysisRequest, analysis_id)
            assert record is not None
            record.status = AnalysisStatus.COMPLETED.value
            record.root_cause = redact(output.root_cause)
            record.explanation = redact(output.explanation)
            record.confidence = output.confidence
            record.warnings = request.warnings
            record.context_files = list(request.files)
            record.attempts = attempts
            record.completed_at = utcnow()
            record.duration_ms = int((time.monotonic() - started) * 1000)
            if candidate is not None and candidate.diff:
                session.add(self._proposal(record, run, candidate))
        metrics.ANALYSES_TOTAL.labels(provider.name, "completed").inc()
        log.info(
            "analysis completed",
            extra={
                "analysis_id": analysis_id,
                "attempts": len(attempts),
                "outcome": attempts[-1]["outcome"] if attempts else None,
            },
        )

    @staticmethod
    def _elapsed(since: float) -> dict[str, int]:
        return {"duration_ms": int((time.monotonic() - since) * 1000)}

    @staticmethod
    def _write_context(workspace: Path, files: dict[str, str]) -> None:
        make_private_dir(workspace)
        for rel, content in files.items():
            target = resolve_within(workspace, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    def _save_progress(self, analysis_id: int, attempts: list[dict[str, Any]]) -> None:
        with self.db.transaction() as session:
            record = session.get(AnalysisRequest, analysis_id)
            assert record is not None
            record.attempts = list(attempts)

    def _check_patch(self, source_dir: Path, diff: str) -> list[str]:
        """Static validation plus a dry-run apply against a copy of the snapshot."""
        report = validate_patch(diff)
        if not report.valid:
            return list(report.errors)
        with tempfile.TemporaryDirectory(
            prefix="devflow-check-", dir=self.settings.resolved_data_dir
        ) as tmp:
            copy = Path(tmp) / "src"
            shutil.copytree(source_dir, copy)
            try:
                apply_patch(copy, diff, check_only=True)
            except ValidationFailed as exc:
                return [exc.message]
        return []

    def _patched_source(self, repo: Repository, base_run: TestRun, diff: str) -> SourcePreparer:
        repo_path = self.repos.path_of(repo)
        sha = base_run.source_info.get("sha")
        if not sha:
            raise ValidationFailed("Base run has no recorded commit")

        def prepare(dest: Path) -> dict[str, Any]:
            info = git.snapshot(repo_path, sha, dest)
            apply_patch(dest, diff)
            return {"files": info.files, "bytes": info.bytes, "patched": True}

        return prepare

    def _test_feedback(self, diff: str, check: TestRun) -> str:
        failures = self.runs.failures(check.id)[:MAX_FEEDBACK_FAILURES]
        lines = [
            f"DevFlow applied your change and ran the test suite in the sandbox: "
            f"{check.passed} passed, {check.failed + check.errors} failed"
            + (f" ({check.error_message})" if check.error_message else "")
            + ".",
            "",
            "Tests still failing:",
        ]
        for failure in failures:
            lines.append(
                f"- {failure['nodeid'] or 'run'} [{failure['category']}] {failure['summary']}"
            )
            if failure["traceback"]:
                lines.append("```\n" + failure["traceback"][-MAX_FEEDBACK_TRACEBACK:] + "\n```")
        lines += ["", "Your previous change was:", "```diff", diff[:MAX_FEEDBACK_DIFF], "```"]
        return "\n".join(lines)

    def _build_input(self, run: TestRun, repo: Repository) -> AnalysisInput:
        source_dir = self.settings.runs_dir / str(run.id) / "src"
        if not source_dir.is_dir():
            raise ValidationFailed("The source snapshot for this run is no longer available")
        with self.db.session() as session:
            failures = list(
                session.scalars(
                    select(Failure)
                    .where(Failure.run_id == run.id)
                    .order_by(Failure.id)
                    .limit(MAX_FAILURES_IN_CONTEXT)
                )
            )
            tracebacks = {
                r.id: r.traceback
                for r in session.scalars(
                    select(TestResult).where(
                        TestResult.id.in_([f.test_result_id for f in failures if f.test_result_id])
                    )
                )
            }
        contexts = [
            FailureContext(
                nodeid=f.nodeid,
                category=f.category,
                summary=f.summary,
                exception_type=f.exception_type,
                file=f.file,
                line=f.line,
                traceback=tracebacks.get(f.test_result_id) if f.test_result_id else None,
            )
            for f in failures
        ]
        files = load_files(source_dir, select_files(contexts))
        warnings = scan_for_injection([*files.values(), *(c.traceback or "" for c in contexts)])
        return AnalysisInput(run.id, repo.name, repo.is_example, contexts, files, warnings)

    def _proposal(
        self, record: AnalysisRequest, run: TestRun, candidate: _Candidate
    ) -> PatchProposal:
        report = validate_patch(candidate.diff)
        return PatchProposal(
            analysis_id=record.id,
            run_id=run.id,
            status=PatchStatus.INVALID.value if candidate.errors else PatchStatus.PROPOSED.value,
            diff=normalize_diff(candidate.diff),
            files=report.files,
            rationale=redact(candidate.rationale) if candidate.rationale else None,
            risk_level=report.risk_level,
            risk_score=report.risk_score,
            risk_reasons=report.risk_reasons,
            validation_errors=candidate.errors,
            lines_added=report.added,
            lines_removed=report.removed,
            verification_run_id=candidate.verification_run_id,
            attempt=candidate.attempt,
        )

    def _fail(
        self,
        analysis_id: int,
        started: float,
        message: str,
        attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        with self.db.transaction() as session:
            record = session.get(AnalysisRequest, analysis_id)
            assert record is not None
            record.status = AnalysisStatus.FAILED.value
            record.attempts = attempts or []
            record.error = redact(message)[:2000]
            record.completed_at = utcnow()
            record.duration_ms = int((time.monotonic() - started) * 1000)
            provider = record.provider
        metrics.ANALYSES_TOTAL.labels(provider, "failed").inc()
        log.warning("analysis failed", extra={"analysis_id": analysis_id, "error": message[:300]})

    def get(self, analysis_id: int) -> AnalysisRequest:
        with self.db.session() as session:
            record = session.get(AnalysisRequest, analysis_id)
            if record is None:
                raise NotFound(f"Analysis {analysis_id} not found")
            _ = record.patches
            return record

    def list_analyses(self, run_id: int | None = None, limit: int = 50) -> list[AnalysisRequest]:
        stmt = select(AnalysisRequest).order_by(AnalysisRequest.id.desc()).limit(limit)
        if run_id is not None:
            stmt = stmt.where(AnalysisRequest.run_id == run_id)
        with self.db.session() as session:
            records = list(session.scalars(stmt))
            for r in records:
                _ = r.patches
            return records

    # ---- patch review ---------------------------------------------------------------

    def get_patch(self, patch_id: int) -> PatchProposal:
        with self.db.session() as session:
            patch = session.get(PatchProposal, patch_id)
            if patch is None:
                raise NotFound(f"Patch {patch_id} not found")
            _ = patch.decisions, patch.analysis
            return patch

    def list_patches(
        self, run_id: int | None = None, status: str | None = None
    ) -> list[PatchProposal]:
        stmt = select(PatchProposal.id).order_by(PatchProposal.id.desc()).limit(200)
        if run_id is not None:
            stmt = stmt.where(PatchProposal.run_id == run_id)
        if status:
            stmt = stmt.where(PatchProposal.status == status)
        with self.db.session() as session:
            ids = list(session.scalars(stmt))
        return [self.get_patch(i) for i in ids]

    def review(self, patch_id: int, decision: str, comment: str | None = None) -> PatchProposal:
        if decision not in ("approve", "reject"):
            raise ValidationFailed("decision must be 'approve' or 'reject'")
        with self.db.transaction() as session:
            patch = session.get(PatchProposal, patch_id)
            if patch is None:
                raise NotFound(f"Patch {patch_id} not found")
            if patch.status != PatchStatus.PROPOSED.value:
                raise Conflict(f"Patch {patch_id} is {patch.status} and cannot be reviewed again")
            patch.status = (
                PatchStatus.APPROVED.value if decision == "approve" else PatchStatus.REJECTED.value
            )
            patch.decided_at = utcnow()
            session.add(ApprovalDecision(patch_id=patch_id, decision=decision, comment=comment))
            base_run = session.get(TestRun, patch.run_id)
            assert base_run is not None
            repo = session.get(Repository, base_run.repository_id)
            assert repo is not None
            diff = patch.diff
        metrics.PATCH_DECISIONS.labels(decision).inc()
        log.info("patch reviewed", extra={"patch_id": patch_id, "decision": decision})
        if decision == "approve":
            try:
                self._start_retest(patch_id, repo, base_run, diff)
            except DevFlowError as exc:
                # The approval stands; the retest can be started again from the UI/API.
                log.warning(
                    "retest could not start", extra={"patch_id": patch_id, "error": exc.message}
                )
        return self.get_patch(patch_id)

    def _start_retest(self, patch_id: int, repo: Repository, base_run: TestRun, diff: str) -> None:
        prepare = self._patched_source(repo, base_run, diff)
        run = self.runs.submit_patch_retest(repo, base_run, patch_id, prepare)
        with self.db.transaction() as session:
            patch = session.get(PatchProposal, patch_id)
            assert patch is not None
            patch.status = PatchStatus.RETESTING.value
            patch.retest_run_id = run.id

    def retest(self, patch_id: int) -> PatchProposal:
        """Re-run tests for an approved patch (e.g. after the runner was unavailable)."""
        patch = self.get_patch(patch_id)
        if patch.status not in (PatchStatus.APPROVED.value, PatchStatus.RETEST_FAILED.value):
            raise Conflict(
                f"Patch {patch_id} is {patch.status}; only approved patches can be retested"
            )
        base_run = self.runs.get(patch.run_id)
        self._start_retest(patch_id, base_run.repository, base_run, patch.diff)
        return self.get_patch(patch_id)

    def export_patch(
        self, patch_id: int, confirm: bool, branch: str | None = None
    ) -> PatchProposal:
        """Write the approved, retested patch to a new branch in the user's repository."""
        if not confirm:
            raise ValidationFailed("Exporting modifies your repository; resend with confirm=true")
        patch = self.get_patch(patch_id)
        if patch.status != PatchStatus.RETEST_PASSED.value:
            raise Conflict("Only approved patches whose retest passed can be exported")
        base_run = self.runs.get(patch.run_id)
        repo_path = self.repos.path_of(base_run.repository)
        sha = str(base_run.source_info["sha"])
        branch = branch or f"devflow/patch-{patch_id}"
        report = validate_patch(patch.diff)
        with tempfile.TemporaryDirectory(
            prefix="devflow-export-", dir=self.settings.resolved_data_dir
        ) as tmp:
            work = Path(tmp) / "src"
            git.snapshot(repo_path, sha, work)
            apply_patch(work, patch.diff)
            changes: dict[str, bytes | None] = {}
            for rel in report.files:
                target = resolve_within(work, rel)
                changes[rel] = target.read_bytes() if target.is_file() else None
        commit = git.create_branch_from_files(
            repo_path,
            sha,
            changes,
            branch,
            f"DevFlow AI: apply reviewed patch #{patch_id}\n\nApproved and retested in DevFlow run #{patch.retest_run_id}.\n",
        )
        with self.db.transaction() as session:
            fresh = session.get(PatchProposal, patch_id)
            assert fresh is not None
            fresh.status = PatchStatus.EXPORTED.value
            fresh.exported_branch = branch
            fresh.exported_commit = commit
            session.add(
                ApprovalDecision(patch_id=patch_id, decision="export", comment=f"branch {branch}")
            )
        log.info("patch exported", extra={"patch_id": patch_id, "branch": branch})
        return self.get_patch(patch_id)
