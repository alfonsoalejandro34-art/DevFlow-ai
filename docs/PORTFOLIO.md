# Portfolio notes

## One-paragraph summary

DevFlow AI is a local developer-productivity platform. It runs a repository's pytest
suite in a locked-down Docker sandbox, classifies failures, and tracks them across
runs. It can optionally ask Claude Code or Codex for a diagnosis and a minimal patch.
Every patch is statically validated, risk-scored, approved by a human, and retested in
isolation before it can reach the repository, and then only as a new branch. It
includes a FastAPI backend, a React/TypeScript dashboard, a CLI, Prometheus metrics,
OpenTelemetry traces, and GitHub Actions CI.

## Resume bullets (verified functionality only)

- Built **DevFlow AI**, a full-stack developer-productivity platform (Python/FastAPI,
  SQLAlchemy/Alembic, React/TypeScript). It automates the test-failure feedback loop:
  isolated test execution, failure classification, AI-assisted diagnosis, and a
  human-approved fix workflow.
- Designed a **sandboxed test runner** (no network, read-only root filesystem, dropped
  capabilities, non-root, CPU/memory/PID limits). A validator refuses any container
  command that weakens isolation, and 10 mutation tests prove each weakening is caught.
- Integrated **Claude Code and Codex CLIs** behind a provider interface with a non-AI
  fallback. Agents get no tools or a read-only sandbox, redacted minimal context, and
  prompt-injection screening. API keys are stripped so analyses never incur metered
  charges. Verified live: both identified all 4 seeded bugs, in 24 s and 18 s.
- Implemented a **patch safety pipeline**: path/binary/symlink validation, a risk score
  that flags test weakening and dangerous calls, dry-run apply, human approval,
  sandboxed retest on a fresh snapshot, and export to a new Git branch via plumbing,
  never touching the working tree. It caught a malformed AI-generated diff in live use.
- Hardened Git access against **hostile repositories**: no hooks, filters, or
  fsmonitor, and snapshots built from raw blobs. Tests plant malicious hooks and filter
  drivers and assert they never execute.
- Wrote **134 backend tests (85% coverage) and 9 frontend tests** covering 13 failure
  and security scenarios end to end (timeouts, cancellation, duplicate jobs, crash
  recovery, path traversal, revision injection). Testing found and fixed 7 real bugs.
- Added **observability** (structured JSON logs with correlation IDs, Prometheus
  metrics, OpenTelemetry traces) and a **benchmark suite** with a documented method. It
  reported honestly that the platform adds ~190 ms over raw pytest on small suites.
- Set up **least-privilege GitHub Actions** (read-only token, no secrets, fork-safe
  trigger) running lint, strict mypy, tests, a Docker sandbox job, Bandit, pip-audit,
  and npm audit.

## Interview talking points

1. **Why not just run the tests?** The value is in what happens after a failure:
   classification, history (repeated vs resolved failures), comparison against a
   baseline, and a review trail. DevFlow is slower than raw pytest, and the benchmarks
   say so.
2. **Threat model first.** List the untrusted inputs (repo content, test output, AI
   output, API input), then map each control to one of them. Walk through the
   hostile-`.git/config` test: `core.fsmonitor` can execute a command on `git status`,
   which is why DevFlow never runs it.
3. **AI is advisory, never authoritative.** Claude gets `--tools ""`, so it can only
   return text. The real boundary is human approval plus a sandboxed retest, not
   prompt-injection detection. Codex's read-only sandbox still allows reads; that is
   documented as a residual risk.
4. **Duplicate-job prevention without locks.** A nullable `UNIQUE active_key` column
   makes the database the arbiter. Eight concurrent submissions produce exactly one
   success, which is tested with a thread barrier.
5. **Bugs testing found.** pytest walked up to the *parent project's* config (fixed by
   pinning rootdir and config). `--pid` matched `--pids-limit` as a substring. SQLite
   id reuse could surface a stale JUnit file. Patch verdicts were computed lazily, so
   metrics went stale (fixed transactionally, plus a data migration). Each one has a
   regression test.
6. **Simplicity as a decision.** In-process thread pool plus SQLite instead of
   Celery/Redis/Postgres, because there is one user and short jobs. The interfaces
   (`Runner`, `Provider`) are where scale-out would plug in: remote runners and other
   agents.
7. **Relevance to AV developer productivity.** Large autonomy codebases have long, flaky,
   resource-heavy test suites. The same loop applies: isolate, classify, fingerprint
   recurring failures, and gate automated fixes behind review. The design extends to
   remote runners and simulation workloads through the runner interface.

## Implementation checklist

| Area | Status |
|---|---|
| Environment inspection, isolated venv, dedicated Git repo | Done |
| Repository registration, validation, structure detection, commits, revisions | Done, verified |
| Test execution, results, logs, timeouts, cancellation, duplicates, history, compare | Done, verified (local runner) |
| Docker sandbox runner | Implemented and unit-tested; **live run pending Docker install** |
| Failure classification and repeated/resolved tracking | Done, verified |
| AI providers (Claude Code, Codex), heuristic fallback, scripted demo | Done, verified live |
| Patch validation, risk, approval, retest, export | Done, verified |
| REST API, auth, error handling, OpenAPI | Done, verified |
| Dashboard (overview, repos, builds, run analysis, reviews, analytics, settings) | Done, verified with screenshots |
| CLI | Done, verified |
| Metrics, logs, traces, health warnings | Done, verified |
| Benchmarks | Done for local runner; **Docker batch pending** |
| GitHub Actions | Written and schema-validated; **not yet run on GitHub** (no push) |
| Security checks (Bandit, pip-audit, npm audit) | Executed, clean |
| Clean-clone setup | Verified (`make setup && make test`, server boot + demo) |
| App `Dockerfile` / `compose.yaml` | Written; **not built** (no Docker) |
| Optional: Go service, gRPC, Kubernetes, SSH runners | Not implemented (core first, as specified) |
