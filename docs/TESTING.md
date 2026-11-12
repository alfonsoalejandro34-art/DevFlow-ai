# Testing

```bash
make test           # backend (not Docker) + frontend
make test-docker    # live sandbox tests (Docker + `make runner-image`)
make check          # lint, mypy, tests, security: what CI runs
```

## Strategy

Tests exercise real behavior: real Git repositories created in `tmp_path`, real pytest
processes started by the real job system, real JUnit parsing, real `git apply`, and real
SQLite with the real migrations. Two things are replaced:

- **AI providers.** A `FakeAIProvider` stands in for the Claude/Codex CLIs, so tests
  don't need network access, a login, or subscription usage. The CLI providers'
  *command construction* is still tested by intercepting `run_command`.
- **The runner, in most workflow tests.** They use the `local` runner so the suite runs
  without Docker. The Docker runner is covered by command-construction and
  failure-mode unit tests (a fake `docker` binary), and by live tests in
  `test_docker_sandbox.py`, which skip automatically when no engine or image is present.

Workflow tests are not mocked "happy paths". They assert specific counts, categories,
file and line locations, state transitions, and that user repositories stay unchanged.

## Suites

| File | Tests | Covers |
|---|---|---|
| `test_workflow.py` | 19 | End to end through the HTTP API: every scenario repo, cancel, duplicates, wall-clock timeout, revision selection, compare, failure resolution, demo/AI/rejected/invalid/malicious patches, approval → retest → export, provider fallback, AI disabled, crash recovery, pytest config isolation, analytics |
| `test_api.py` | 30 | Auth (5 bad-token variants), Host header, correlation IDs, error format, malicious paths (7), approved roots, unsupported repos, settings bounds (7), OpenAPI, loopback enforcement |
| `test_security.py` | 51 | Path validation and traversal, symlink escapes, hostile git config/filters/hooks, revision injection (6), patch validation (9 malicious shapes), risk scoring, model-diff normalization, redaction, command allowlist, env scrubbing, token file |
| `test_components.py` | 34 | JUnit parsing (including entity-expansion attack), classification (7), signatures, structure detection, Docker command flags and 10 weakening mutations, Docker failure (exit 125), runner unavailable (503), CLI invocation flags, prompt construction/injection, response parsing, DB constraints, concurrent duplicate submissions |
| `test_docker_sandbox.py` | 5 | Live: UID, no network, read-only `/src` and rootfs, zero capabilities, NoNewPrivs, noexec `/tmp`, examples in Docker, memory limit, cancellation + container cleanup, wall-clock timeout |
| `frontend/src/__tests__` | 9 | Status badges are labeled, diff rendering, formatting, sign-in errors and bearer header, approval requires an explicit click and posts the decision, invalid patches cannot be approved, export disabled until acknowledged, build history filters |

## Required scenarios

| # | Scenario | Test |
|---|---|---|
| 1 | All tests pass | `test_passing_repository` |
| 2 | Failing tests | `test_failing_repository_records_classified_failures` |
| 3 | Syntax errors | `test_syntax_error_repository` |
| 4 | Missing dependencies | `test_missing_dependency_repository` |
| 5 | Timeout | `test_hanging_test_is_stopped_by_per_test_timeout`, `test_wall_clock_timeout_kills_run`, `test_wall_clock_timeout_in_docker` |
| 6 | Cancel a running test | `test_cancel_running_job`, `test_cancel_kills_container` |
| 7 | Docker execution failure | `test_docker_execution_failure_is_reported_as_infrastructure_error`, `test_docker_failure_through_api`, `test_no_runner_available_returns_503` |
| 8 | Unavailable AI provider | `test_unavailable_ai_provider_degrades_gracefully`, `test_cli_provider_missing_binary` |
| 9 | Proposal rejected | `test_rejected_ai_proposal` |
| 10 | Approved patch still fails | `test_approved_patch_that_still_fails` |
| 11 | Malicious repository paths | `test_register_malicious_paths`, `test_rejects_*` |
| 12 | Unauthorized file access | `test_rejects_symlink_escaping_root`, `test_resolve_within_blocks_symlink_escape`, `test_snapshot_skips_symlinks_and_submodules`, `test_logs_endpoint_validates_stream`, `test_malicious_patch_from_provider_is_rejected` |
| 13 | Restricted host commands | `test_command_allowlist`, `test_revision_injection_rejected`, `test_hostile_git_config_never_executes`, `test_settings_cannot_enable_network_or_change_runner` |

## Results on the development machine

- Backend: **134 passed**, 5 Docker tests skipped (no Docker engine), 85% line coverage.
- Frontend: **9 passed**.
- `ruff check`, `ruff format --check`, `mypy --strict` (50 files), `tsc`: clean.

## Bugs found by testing

Live testing caught issues the unit tests had not, and each now has a regression test:

1. The Docker flag validator matched `--pid` as a substring of `--pids-limit` and
   refused every container.
2. Classification called a `ZeroDivisionError` an assertion because `assert` appeared
   in the echoed test source.
3. **pytest inherited a parent directory's configuration** when the data directory was
   inside another project. The rootdir and config are now pinned.
4. Collection-error node IDs were dotted module names instead of file paths.
5. A run could reuse a deleted run's directory (SQLite id reuse) and read a stale
   `junit.xml`. Run directories are now cleared at start.
6. A real Codex diff was rejected because a file header carried a stray leading space.
   It is now normalized.
7. A patch's retest verdict was only recorded when someone opened the patch, so the
   analytics showed stale "retesting" rows. The verdict is now written in the same
   transaction that finishes the retest run, and migration `0002` repairs existing rows.
