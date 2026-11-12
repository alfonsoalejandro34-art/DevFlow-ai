"""Application factory: wires services together and builds the FastAPI app."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from devflow import __version__
from devflow.ai.base import Provider
from devflow.ai.registry import build_providers
from devflow.config import Settings, get_settings
from devflow.database.session import Database
from devflow.errors import DevFlowError
from devflow.observability.logging import configure_logging, correlation_id, new_correlation_id
from devflow.observability.metrics import HTTP_LATENCY
from devflow.observability.tracing import configure_tracing
from devflow.runners.base import Runner
from devflow.runners.docker import DockerRunner
from devflow.runners.local import LocalRunner
from devflow.security.auth import load_or_create_token, token_matches
from devflow.security.paths import make_private_dir
from devflow.services.analysis import AnalysisService
from devflow.services.analytics import AnalyticsService
from devflow.services.repos import RepositoryService
from devflow.services.runs import RunService
from devflow.services.settings import SettingsService
from devflow.workers.jobs import JobManager

log = logging.getLogger(__name__)
_CID_PATTERN = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
_PUBLIC_PATHS = {"/api/health", "/metrics", "/docs", "/openapi.json", "/redoc"}


@dataclass
class Container:
    settings: Settings
    db: Database
    token: str
    runtime: SettingsService
    repos: RepositoryService
    runs: RunService
    analysis: AnalysisService
    analytics: AnalyticsService
    jobs: JobManager
    runner: Runner


def build_runner(settings: Settings) -> Runner:
    if settings.runner == "local":
        # Settings validation guarantees allow_unsandboxed_runner is set here.
        return LocalRunner()
    return DockerRunner(settings.runner_image, settings.docker_binary)


def build_container(
    settings: Settings,
    runner: Runner | None = None,
    providers: dict[str, Provider] | None = None,
) -> Container:
    data_dir = make_private_dir(settings.resolved_data_dir)
    for sub in (settings.runs_dir, settings.ai_workspaces_dir, settings.managed_repos_dir):
        make_private_dir(sub)
    db = Database(settings.sqlalchemy_url)
    db.migrate()
    runtime = SettingsService(db, settings)
    jobs = JobManager(settings.max_concurrent_jobs)
    runner = runner or build_runner(settings)
    repos = RepositoryService(db, settings, runtime)
    runs = RunService(db, settings, runtime, repos, runner, jobs)
    analysis = AnalysisService(
        db, settings, runtime, repos, runs, providers or build_providers(settings), jobs
    )
    token = load_or_create_token(
        data_dir, settings.api_token.get_secret_value() if settings.api_token else None
    )
    return Container(
        settings, db, token, runtime, repos, runs, analysis, AnalyticsService(db), jobs, runner
    )


def create_app(
    settings: Settings | None = None,
    runner: Runner | None = None,
    providers: dict[str, Provider] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    container = build_container(settings, runner, providers)
    tracer_provider = configure_tracing(settings.otel_exporter, settings.resolved_data_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        recovered = container.runs.recover_interrupted()
        if recovered:
            log.warning("marked interrupted runs", extra={"count": recovered})
        log.info("devflow started", extra={"runner": container.runner.name, "version": __version__})
        yield
        container.jobs.shutdown()
        if tracer_provider is not None:
            tracer_provider.shutdown()
        container.db.dispose()

    app = FastAPI(
        title="DevFlow AI",
        version=__version__,
        description="Sandboxed test execution, failure analysis, and reviewed AI-assisted fixes. "
        "All `/api` routes except `/api/health` require `Authorization: Bearer <token>`; "
        "get the token with `devflow token`.",
        lifespan=lifespan,
    )
    app.state.container = container

    hosts = ["127.0.0.1", "localhost", "testserver", "[::1]"]
    if settings.allow_remote_bind:
        hosts.append(settings.host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)  # blocks DNS rebinding
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("x-correlation-id", "")
        cid = supplied if _CID_PATTERN.match(supplied) else new_correlation_id()
        token = correlation_id.set(cid)
        started = time.perf_counter()
        try:
            path = request.url.path
            if (
                path.startswith("/api")
                and path not in _PUBLIC_PATHS
                and request.method != "OPTIONS"
                and not token_matches(container.token, request.headers.get("authorization"))
            ):
                response: Response = _error(401, "unauthorized", "Missing or invalid API token")
            else:
                response = await call_next(request)
            route = request.scope.get("route")
            HTTP_LATENCY.labels(
                request.method, getattr(route, "path", "unmatched"), str(response.status_code)
            ).observe(time.perf_counter() - started)
            response.headers["X-Correlation-ID"] = cid
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            correlation_id.reset(token)

    @app.exception_handler(DevFlowError)
    async def devflow_error(_request: Request, exc: DevFlowError) -> JSONResponse:
        if exc.status_code >= 500:
            log.warning("request failed", extra={"code": exc.code, "error": exc.message})
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        return _error(422, "validation_failed", f"{where}: {first.get('msg', 'invalid request')}")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "internal_error", "Internal server error")

    from devflow.api.routes import router

    app.include_router(router)
    _instrument(app)
    _mount_frontend(app, settings)
    return app


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "correlation_id": correlation_id.get()}
        },
    )


def _instrument(app: FastAPI) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,api/health")
    except Exception:  # instrumentation is optional
        log.warning("FastAPI OpenTelemetry instrumentation unavailable")


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.is_file():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise StarletteHTTPException(404, "Not found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
