"""REST API routes. Business logic lives in ``devflow.services``; handlers only translate."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text

from devflow import __version__
from devflow.api import schemas
from devflow.app import Container
from devflow.database.models import AnalysisRequest, PatchProposal, TestRun, utcnow
from devflow.observability.logging import set_level
from devflow.observability.metrics import REGISTRY
from devflow.services.runs import RunFilters
from devflow.services.settings import RuntimeConfig

router = APIRouter()
_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"model": schemas.ErrorResponse} for code in (401, 403, 404, 409, 422, 503)
}


def _c(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def _run_out(run: TestRun) -> schemas.RunOut:
    out = schemas.RunOut.model_validate(run)
    out.repository_name = run.repository.name if run.repository else None
    return out


def _patch_out(patch: PatchProposal) -> schemas.PatchOut:
    out = schemas.PatchOut.model_validate(patch)
    out.provider = patch.analysis.provider
    out.is_ai = patch.analysis.is_ai
    return out


def _analysis_out(record: AnalysisRequest) -> schemas.AnalysisOut:
    out = schemas.AnalysisOut.model_validate(record)
    out.patch_ids = [p.id for p in record.patches]
    return out


# ---- system -----------------------------------------------------------------------------


@router.get("/api/health", tags=["system"])
def health(request: Request) -> dict[str, Any]:
    """Liveness plus degraded-service warnings. Does not require authentication."""
    c = _c(request)
    warnings: list[dict[str, str]] = []
    db_ok = True
    try:
        with c.db.session() as session:
            session.execute(text("SELECT 1"))
            recent_errors = (
                session.scalar(
                    select(func.count())
                    .select_from(TestRun)
                    .where(
                        TestRun.status == "error",
                        TestRun.finished_at >= utcnow() - timedelta(hours=24),
                    )
                )
                or 0
            )
    except Exception:
        db_ok, recent_errors = False, 0
        warnings.append({"level": "error", "message": "Database is unreachable"})
    runner = c.runner.availability()
    if not runner.available:
        warnings.append({"level": "error", "message": f"Test runner unavailable: {runner.reason}"})
    if not c.runner.sandboxed:
        warnings.append(
            {
                "level": "warning",
                "message": "Unsandboxed local runner is active: tests execute directly on this machine",
            }
        )
    if recent_errors:
        warnings.append(
            {
                "level": "warning",
                "message": f"{recent_errors} run(s) ended with an infrastructure error in the last 24h",
            }
        )
    status = "ok" if db_ok and runner.available else "degraded"
    return {
        "status": status,
        "version": __version__,
        "database": db_ok,
        "runner": {
            "name": c.runner.name,
            "sandboxed": c.runner.sandboxed,
            "available": runner.available,
            "reason": runner.reason,
            "version": runner.version,
        },
        "active_jobs": len(c.jobs.active_keys()),
        "warnings": warnings,
    }


@router.get("/metrics", tags=["system"], include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/settings", tags=["system"], responses=_ERRORS)
def get_settings(request: Request) -> dict[str, Any]:
    c = _c(request)
    return {
        "runtime": c.runtime.get().model_dump(),
        "static": {
            "runner": c.runner.name,
            "sandboxed": c.runner.sandboxed,
            "runner_image": c.settings.runner_image,
            "network_access": "disabled (not configurable)",
            "bind": f"{c.settings.host}:{c.settings.port}",
            "data_dir": str(c.settings.resolved_data_dir),
            "managed_repos_dir": str(c.settings.managed_repos_dir),
            "env_allowed_roots": [str(p) for p in c.settings.allowed_roots],
            "max_concurrent_jobs": c.settings.max_concurrent_jobs,
        },
    }


@router.put("/api/settings", tags=["system"], responses=_ERRORS)
def update_settings(request: Request, body: RuntimeConfig) -> dict[str, Any]:
    c = _c(request)
    c.runtime.update(body)
    set_level(body.log_level)
    return get_settings(request)


# ---- repositories -----------------------------------------------------------------------


@router.get("/api/repositories", tags=["repositories"], responses=_ERRORS)
def list_repositories(request: Request) -> list[schemas.RepositoryOut]:
    return [schemas.RepositoryOut.model_validate(r) for r in _c(request).repos.list_all()]


@router.post("/api/repositories", status_code=201, tags=["repositories"], responses=_ERRORS)
def register_repository(request: Request, body: schemas.RepositoryCreate) -> schemas.RepositoryOut:
    return schemas.RepositoryOut.model_validate(_c(request).repos.register(body.path, body.name))


@router.post(
    "/api/repositories/examples", status_code=201, tags=["repositories"], responses=_ERRORS
)
def install_examples(request: Request) -> list[schemas.RepositoryOut]:
    """Install the bundled example repositories into DevFlow's managed directory."""
    return [schemas.RepositoryOut.model_validate(r) for r in _c(request).repos.install_examples()]


@router.get("/api/repositories/{repo_id}", tags=["repositories"], responses=_ERRORS)
def get_repository(request: Request, repo_id: int) -> schemas.RepositoryDetail:
    c = _c(request)
    repo = c.repos.get(repo_id)
    info = c.repos.inspect(repo_id)
    return schemas.RepositoryDetail.model_validate(
        {**schemas.RepositoryOut.model_validate(repo).model_dump(), **info}
    )


@router.post("/api/repositories/{repo_id}/refresh", tags=["repositories"], responses=_ERRORS)
def refresh_repository(request: Request, repo_id: int) -> schemas.RepositoryOut:
    return schemas.RepositoryOut.model_validate(_c(request).repos.refresh_structure(repo_id))


@router.get("/api/repositories/{repo_id}/commits", tags=["repositories"], responses=_ERRORS)
def list_commits(
    request: Request,
    repo_id: int,
    rev: str = Query("HEAD", max_length=200),
    limit: int = Query(20, ge=1, le=200),
) -> list[schemas.CommitOut]:
    from devflow.services import git

    c = _c(request)
    path = c.repos.path_of(c.repos.get(repo_id))
    return [schemas.CommitOut(**commit.__dict__) for commit in git.recent_commits(path, rev, limit)]


@router.delete(
    "/api/repositories/{repo_id}", status_code=204, tags=["repositories"], responses=_ERRORS
)
def unregister_repository(request: Request, repo_id: int) -> Response:
    """Remove the registration and its history. Files on disk are not touched."""
    _c(request).repos.unregister(repo_id)
    return Response(status_code=204)


# ---- runs ---------------------------------------------------------------------------------


@router.post("/api/runs", status_code=202, tags=["runs"], responses=_ERRORS)
def create_run(request: Request, body: schemas.RunCreate) -> schemas.RunOut:
    c = _c(request)
    run = c.runs.submit(
        body.repository_id,
        body.revision,
        trigger=request.headers.get("x-devflow-client", "api")[:20],
    )
    return _run_out(c.runs.get(run.id))


@router.get("/api/runs", tags=["runs"], responses=_ERRORS)
def list_runs(
    request: Request,
    repository_id: int | None = None,
    status: Literal["queued", "running", "passed", "failed", "error", "timeout", "cancelled"]
    | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> schemas.RunPage:
    runs, total = _c(request).runs.search(RunFilters(repository_id, status, q, limit, offset))
    return schemas.RunPage(
        items=[_run_out(r) for r in runs], total=total, limit=limit, offset=offset
    )


@router.get("/api/runs/{run_id}", tags=["runs"], responses=_ERRORS)
def get_run(request: Request, run_id: int) -> schemas.RunOut:
    return _run_out(_c(request).runs.get(run_id))


@router.post("/api/runs/{run_id}/cancel", tags=["runs"], responses=_ERRORS)
def cancel_run(request: Request, run_id: int) -> schemas.RunOut:
    return _run_out(_c(request).runs.cancel(run_id))


@router.get("/api/runs/{run_id}/results", tags=["runs"], responses=_ERRORS)
def run_results(request: Request, run_id: int) -> list[schemas.TestResultOut]:
    return [schemas.TestResultOut.model_validate(r) for r in _c(request).runs.results(run_id)]


@router.get("/api/runs/{run_id}/failures", tags=["runs"], responses=_ERRORS)
def run_failures(request: Request, run_id: int) -> list[schemas.FailureOut]:
    return [schemas.FailureOut(**f) for f in _c(request).runs.failures(run_id)]


@router.get("/api/runs/{run_id}/logs", tags=["runs"], responses=_ERRORS)
def run_logs(
    request: Request, run_id: int, stream: Literal["stdout", "stderr"] = "stdout"
) -> schemas.LogOut:
    return schemas.LogOut(
        run_id=run_id, stream=stream, content=_c(request).runs.logs(run_id, stream)
    )


@router.get("/api/runs/{run_id}/compare", tags=["runs"], responses=_ERRORS)
def compare_runs(request: Request, run_id: int, baseline: int | None = None) -> dict[str, Any]:
    """Compare a run with a baseline (default: the run it retests, else the previous run)."""
    return _c(request).runs.compare(run_id, baseline)


# ---- AI analysis and patches -------------------------------------------------------------


@router.get("/api/providers", tags=["analysis"], responses=_ERRORS)
def list_providers(request: Request) -> list[schemas.ProviderOut]:
    return [schemas.ProviderOut(**p) for p in _c(request).analysis.provider_status()]


@router.post("/api/analyses", status_code=202, tags=["analysis"], responses=_ERRORS)
def create_analysis(request: Request, body: schemas.AnalysisCreate) -> schemas.AnalysisOut:
    c = _c(request)
    record = c.analysis.request(body.run_id, body.provider)
    return _analysis_out(c.analysis.get(record.id))


@router.get("/api/analyses", tags=["analysis"], responses=_ERRORS)
def list_analyses(request: Request, run_id: int | None = None) -> list[schemas.AnalysisOut]:
    return [_analysis_out(a) for a in _c(request).analysis.list_analyses(run_id)]


@router.get("/api/analyses/{analysis_id}", tags=["analysis"], responses=_ERRORS)
def get_analysis(request: Request, analysis_id: int) -> schemas.AnalysisOut:
    return _analysis_out(_c(request).analysis.get(analysis_id))


@router.get("/api/patches", tags=["patches"], responses=_ERRORS)
def list_patches(
    request: Request, run_id: int | None = None, status: str | None = Query(None, max_length=20)
) -> list[schemas.PatchOut]:
    return [_patch_out(p) for p in _c(request).analysis.list_patches(run_id, status)]


@router.get("/api/patches/{patch_id}", tags=["patches"], responses=_ERRORS)
def get_patch(request: Request, patch_id: int) -> schemas.PatchOut:
    return _patch_out(_c(request).analysis.get_patch(patch_id))


@router.post("/api/patches/{patch_id}/review", tags=["patches"], responses=_ERRORS)
def review_patch(request: Request, patch_id: int, body: schemas.ReviewCreate) -> schemas.PatchOut:
    """Record a human decision. Approval triggers a sandboxed retest of the patched code;
    the original repository is not modified."""
    return _patch_out(_c(request).analysis.review(patch_id, body.decision, body.comment))


@router.post("/api/patches/{patch_id}/retest", tags=["patches"], responses=_ERRORS)
def retest_patch(request: Request, patch_id: int) -> schemas.PatchOut:
    return _patch_out(_c(request).analysis.retest(patch_id))


@router.post("/api/patches/{patch_id}/export", tags=["patches"], responses=_ERRORS)
def export_patch(request: Request, patch_id: int, body: schemas.ExportCreate) -> schemas.PatchOut:
    """Create a new branch containing the approved, retested patch. Requires ``confirm``.
    The working tree, index, and checked-out branch are never modified."""
    return _patch_out(_c(request).analysis.export_patch(patch_id, body.confirm, body.branch))


# ---- metrics ----------------------------------------------------------------------------


@router.get("/api/metrics/overview", tags=["metrics"], responses=_ERRORS)
def metrics_overview(request: Request) -> dict[str, Any]:
    return _c(request).analytics.overview()


@router.get("/api/metrics/timeseries", tags=["metrics"], responses=_ERRORS)
def metrics_timeseries(request: Request, days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    return _c(request).analytics.timeseries(days)


@router.get("/api/benchmarks", tags=["metrics"], responses=_ERRORS)
def benchmarks(request: Request) -> list[dict[str, Any]]:
    return _c(request).analytics.benchmarks()
