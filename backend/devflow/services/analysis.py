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
from pathlib import Path
from typing import Any

from sqlalchemy import select

from devflow.ai.base import AnalysisInput, FailureContext, Provider, ProviderError
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
from devflow.services.patching import apply_patch
from devflow.services.repos import RepositoryService
from devflow.services.runs import RunService
from devflow.services.settings import SettingsService
from devflow.workers.jobs import JobManager

log = logging.getLogger(__name__)
MAX_FAILURES_IN_CONTEXT = 8
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
        record = AnalysisRequest(run_id=run_id, provider=name, is_ai=provider.is_ai)
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
        workspace = make_private_dir(self.settings.ai_workspaces_dir / str(analysis_id))
        with tracer.start_as_current_span(
            "devflow.analysis", attributes={"analysis_id": analysis_id, "provider": provider.name}
        ):
            try:
                request = self._build_input(run, repo)
                for rel, content in request.files.items():
                    target = resolve_within(workspace, rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                output = provider.analyze(request, workspace)
            except (ProviderError, ValidationFailed, OSError) as exc:
                self._fail(analysis_id, started, str(exc))
                return
            except Exception as exc:
                log.exception("analysis crashed", extra={"analysis_id": analysis_id})
                self._fail(analysis_id, started, f"{type(exc).__name__}: {exc}")
                return
            finally:
                shutil.rmtree(workspace, ignore_errors=True)

        with self.db.transaction() as session:
            record = session.get(AnalysisRequest, analysis_id)
            assert record is not None
            record.status = AnalysisStatus.COMPLETED.value
            record.root_cause = redact(output.root_cause)
            record.explanation = redact(output.explanation)
            record.confidence = output.confidence
            record.warnings = request.warnings
            record.context_files = list(request.files)
            record.completed_at = utcnow()
            record.duration_ms = int((time.monotonic() - started) * 1000)
            if output.patch:
                session.add(self._proposal(record, run, output.patch, output.patch_rationale))
        metrics.ANALYSES_TOTAL.labels(provider.name, "completed").inc()
        log.info(
            "analysis completed",
            extra={"analysis_id": analysis_id, "has_patch": bool(output.patch)},
        )

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
        self, record: AnalysisRequest, run: TestRun, diff: str, rationale: str | None
    ) -> PatchProposal:
        report = validate_patch(diff)
        errors = list(report.errors)
        if report.valid:
            source_dir = self.settings.runs_dir / str(run.id) / "src"
            with tempfile.TemporaryDirectory(
                prefix="devflow-check-", dir=self.settings.resolved_data_dir
            ) as tmp:
                copy = Path(tmp) / "src"
                shutil.copytree(source_dir, copy)
                try:
                    apply_patch(copy, diff, check_only=True)
                except ValidationFailed as exc:
                    errors.append(exc.message)
        return PatchProposal(
            analysis_id=record.id,
            run_id=run.id,
            status=PatchStatus.PROPOSED.value if not errors else PatchStatus.INVALID.value,
            diff=normalize_diff(diff),
            files=report.files,
            rationale=redact(rationale) if rationale else None,
            risk_level=report.risk_level,
            risk_score=report.risk_score,
            risk_reasons=report.risk_reasons,
            validation_errors=errors,
            lines_added=report.added,
            lines_removed=report.removed,
        )

    def _fail(self, analysis_id: int, started: float, message: str) -> None:
        with self.db.transaction() as session:
            record = session.get(AnalysisRequest, analysis_id)
            assert record is not None
            record.status = AnalysisStatus.FAILED.value
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
        self._sync_patch(patch_id)
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
        repo_path = self.repos.path_of(repo)
        sha = base_run.source_info.get("sha")
        if not sha:
            raise ValidationFailed("Base run has no recorded commit")

        def prepare(dest: Path) -> dict[str, Any]:
            info = git.snapshot(repo_path, sha, dest)
            apply_patch(dest, diff)
            return {"files": info.files, "bytes": info.bytes, "patched": True}

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

    def _sync_patch(self, patch_id: int) -> None:
        """Advance RETESTING patches once their retest run has finished."""
        with self.db.transaction() as session:
            patch = session.get(PatchProposal, patch_id)
            if (
                patch is None
                or patch.status != PatchStatus.RETESTING.value
                or patch.retest_run_id is None
            ):
                return
            run = session.get(TestRun, patch.retest_run_id)
            if run is None or not RunStatus(run.status).is_terminal:
                return
            if run.status == RunStatus.PASSED.value:
                patch.status = PatchStatus.RETEST_PASSED.value
            elif run.status in (RunStatus.ERROR.value, RunStatus.CANCELLED.value):
                patch.status = PatchStatus.APPROVED.value  # retest did not produce a verdict
            else:
                patch.status = PatchStatus.RETEST_FAILED.value

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
