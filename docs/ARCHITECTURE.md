# Architecture

DevFlow AI is a single-process FastAPI service with an in-process job pool, a SQLite
database, a pluggable test runner, and pluggable analysis providers. It is a local tool
for one developer. Choosing a simple architecture over distributed components was
deliberate: everything a reviewer needs to understand fits in one Python package.

## Repository layout

```
backend/devflow/
  app.py              application factory: wiring, middleware, auth, error handling
  main.py             ASGI entry point
  cli.py              `devflow` CLI (serve, token, doctor, and API client commands)
  config.py           environment settings (DEVFLOW_*), validated
  errors.py           exception types -> HTTP status codes
  api/                routes.py (thin handlers), schemas.py (Pydantic models)
  database/           models.py (SQLAlchemy), session.py (engine, WAL, migrations)
  migrations/         Alembic environment and versions
  security/           paths, commands (process allowlist), patches (validation),
                      redact, auth
  services/           git, detect, repos, runs, junit, classify, analysis,
                      patching, analytics, settings
  runners/            base (interface + supervision), docker, local
  ai/                 base (interface), context (prompt + injection screening),
                      cli_providers (Claude Code, Codex), heuristic, demo, registry
  workers/jobs.py     bounded thread pool with cancellation
  observability/      logging (JSON + correlation IDs), metrics, tracing
backend/tests/        pytest suite (unit, API, security, workflow, Docker)
frontend/src/         React dashboard (pages, components, charts, services)
runner/               sandbox image (Dockerfile + pinned pytest packages)
examples/             example repositories and the scripted demo fixes
benchmarks/           benchmark suite
scripts/              demo and screenshot automation
```

The prompt's suggested `backend/{app,api,models,...}` layout became one importable
package, `backend/devflow/`, with those concerns as subpackages. That gives one import
root and one mypy/pytest target.

## Components

**API layer** (`api/routes.py`) only translates between HTTP and services. All business
rules live in services, so the CLI, the dashboard, and the tests share one behavior.

**Repository service** validates paths against the approved roots on every use, not
just at registration. It reads Git metadata through `services/git.py`, which disables
hooks, `fsmonitor`, credential helpers, and protocols and ignores global and system
config.

**Run service** owns the run lifecycle:

```
queued ──► running ──► passed | failed | error | timeout | cancelled
   │                       ▲
   └── cancel ─────────────┘   (restart while active ─► error "Interrupted")
```

1. `submit` resolves the revision, then inserts the run with `active_key =
   "<repo>:<sha>:revision"`. That column is `UNIQUE`, so two concurrent identical
   submissions cannot both succeed: one gets HTTP 409. Finishing a run sets the key to
   `NULL`.
2. The job manager runs `_execute` on a worker thread. The run directory is cleared,
   the commit is materialized from raw blobs into `runs/<id>/src`, and the runner
   executes pytest with a pinned rootdir and config, writing JUnit to `runs/<id>/out`.
3. `_record_outcome` parses JUnit (defusedxml, size-capped) and stores `TestResult`
   rows. It classifies each failure into a `Failure` row with a signature (a hash of
   category, test, exception type, and the message with volatile numbers stripped).
   Earlier open failures whose tests now pass are marked resolved.
4. On startup, runs left `queued`/`running` by a previous process are marked
   `error: Interrupted`.

**Runner interface** (`runners/base.py`): `availability()` plus
`run(spec, cancel_event) -> RunOutcome`. A shared `supervise()` loop enforces the
wall-clock timeout, cancellation, and the log-size cap. `DockerRunner` builds and
re-validates the `docker run` command. `LocalRunner` is the unsandboxed opt-in path.
Remote (SSH or Kubernetes) runners would implement the same two methods.

**Analysis service** builds an `AnalysisInput` from the stored failures and the source
files their tracebacks reference, plus the modules under test, with secrets redacted.
It writes only those files to a throwaway workspace and calls a provider. Any returned
diff becomes a `PatchProposal` after validation, risk scoring, and a dry-run `git apply`.

**Agent mode and the self-correcting loop** (`AnalysisService._execute`):

```
for attempt in 1..N:                      N = ai_max_attempts (1 for non-AI providers)
    workspace = copy of runs/<id>/src minus agent-control files
    provider.analyze(agent=True, feedback=previous problems)   # agent edits workspace
    diff = difflib(snapshot, workspace)    # computed by DevFlow, never by the agent
    problems = validate_patch(diff) + dry-run git apply
    if problems:            feedback = "rejected before tests" + problems + diff; continue
    check = RunService.run_verification(...)   # sandboxed run, kind=ai_verification,
                                               # executed inline on the analysis thread
    if check passed:        stop (candidate verified)
    else:                   feedback = counts + failing tests + tracebacks + diff
record every attempt on the analysis; the last candidate becomes a PatchProposal
(attempt number + verification_run_id) awaiting human review
```

Verification runs execute inline rather than being queued. That way an analysis can
never wait on a pool slot it is itself occupying. They are cancellable through the same
job registry, never resolve failures, and are excluded from build metrics.

**Patch workflow**:

```
proposed ──approve──► retesting ──► retest_passed ──export (confirm)──► exported
    │                     │    └──► retest_failed ──retest──► retesting
    ├──reject──► rejected  └──(runner error/cancel)──► approved ──retest──► retesting
invalid (failed validation; cannot be reviewed)
```

Approval applies the patch to a **fresh snapshot** in DevFlow's own run directory and
retests it there. Export is the only operation that writes to a user repository. It
uses Git plumbing (`read-tree`, `hash-object`, `update-index`, `write-tree`,
`commit-tree`, and `update-ref` with an all-zero old value) against a temporary index.
The working tree, index, and HEAD are untouched, and existing branches cannot be
overwritten.

**Analytics service** computes every dashboard metric from database rows on request.
Nothing is precomputed or estimated.

## Database schema

```mermaid
erDiagram
    repositories ||--o{ revisions : has
    repositories ||--o{ test_runs : has
    revisions ||--o{ test_runs : "tested by"
    test_runs ||--o{ test_results : contains
    test_runs ||--o{ failures : records
    test_results ||--o| failures : "classified as"
    test_runs ||--o{ analysis_requests : "analyzed by"
    analysis_requests ||--o{ patch_proposals : proposes
    patch_proposals ||--o{ approval_decisions : "reviewed by"
    patch_proposals ||--o| test_runs : "retested by"
    test_runs ||--o| test_runs : "parent (baseline)"

    repositories { int id PK; string name UK; string path UK; json languages; json structure; bool is_example }
    revisions { int id PK; int repository_id FK; string sha; string ref; string message }
    test_runs { int id PK; int repository_id FK; string kind; string status; string active_key UK; string runner; bool sandboxed; int duration_ms; int test_duration_ms; int overhead_ms; int passed; int failed; string stdout_path; string stderr_path }
    test_results { int id PK; int run_id FK; string nodeid; string outcome; float duration_ms; text traceback }
    failures { int id PK; int run_id FK; string category; string signature; string file; int line; int resolved_by_run_id FK }
    analysis_requests { int id PK; int run_id FK; string provider; bool is_ai; string status; text root_cause; json warnings; int duration_ms }
    patch_proposals { int id PK; int analysis_id FK; string status; text diff; string risk_level; int risk_score; json validation_errors; int retest_run_id FK; string exported_branch }
    approval_decisions { int id PK; int patch_id FK; string decision; string actor; text comment }
    benchmark_results { int id PK; string batch_id; string scenario; string mode; float duration_ms; bool success }
    app_settings { string key PK; json value }
```

- Foreign keys are enforced (`PRAGMA foreign_keys=ON`). Deleting a repository cascades
  to its revisions, runs, results, failures, analyses, and patches.
- Logs are **files** under `runs/<id>/out/` (redacted, capped at 10 MB during the run
  and 256 KB per API response). Only their paths are stored, so large logs are never
  duplicated in the database.
- Timestamps are stored as naive UTC and returned timezone-aware (`UTCDateTime`).
- SQLite runs in WAL mode with a busy timeout. API reads proceed while a job writes,
  and every multi-row change is a single transaction.
- Schema changes go through Alembic (`backend/devflow/migrations`). The app runs
  `upgrade head` on start. Generate a new migration with
  `alembic revision --autogenerate -m "..."`.

## Observability

| Signal | Where | Contents |
|---|---|---|
| Logs | stderr, JSON | `ts, level, logger, message, correlation_id` + structured fields (`run_id`, `analysis_id`, ...), redacted |
| Correlation IDs | `X-Correlation-ID` header (accepted or generated) | propagated into job threads; stored on each run |
| Metrics | `GET /metrics` (Prometheus text) | request latency histogram by route; runs by status/runner; run duration; runner overhead; test outcomes; analyses; review decisions; job errors; active jobs |
| Traces | `<data_dir>/traces.jsonl` (default), console, or OTLP | FastAPI request spans; `devflow.run` → `prepare_source` → `runner.execute` → `parse_results`; `devflow.analysis` |
| Alerts | `GET /api/health` → dashboard banner | runner unavailable, unsandboxed runner active, runs that ended in infrastructure errors in the last 24 h, database unreachable |

To inspect them:

```bash
curl -s 127.0.0.1:8000/metrics | grep devflow_
tail -f .devflow/traces.jsonl | jq '{name, duration_ms, attributes}'
devflow serve 2>&1 | jq 'select(.level=="WARNING")'
```

## Design decisions

- **In-process thread pool, not Celery/Redis.** One user, short jobs. Durable state is
  in SQLite, and restart recovery is explicit. A queue would add a service and failure
  modes without any benefit here.
- **SQLite, not Postgres.** Zero setup, and enough for local use. SQLAlchemy plus
  Alembic keep a later move to Postgres to a configuration change.
- **Snapshots from blobs, not `git worktree`/`checkout`.** Checkout runs hooks and
  filters and modifies `.git`. Reading blobs is side-effect free.
- **Patches retested on a fresh snapshot, not the user's tree.** Approval stays
  reversible until the explicit export step.
- **Pinned pytest rootdir and config.** Without them, pytest walks parent directories
  and can apply a configuration that isn't the repository's own. This was found in live
  testing and is covered by a regression test.
