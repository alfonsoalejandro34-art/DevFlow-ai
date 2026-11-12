# REST API

Base URL `http://127.0.0.1:8000`. Interactive OpenAPI docs are at `/docs` and the
schema is at `/openapi.json`.

**Authentication.** Every `/api/*` route except `/api/health` requires
`Authorization: Bearer <token>`. Print the token with `devflow token`.

**Errors.** All errors share one shape:

```json
{"error": {"code": "conflict", "message": "An identical test run is already queued or running", "correlation_id": "4f0c..."}}
```

| Status | Code | Meaning |
|---|---|---|
| 400 | `http_error` | Host header not allowed |
| 401 | `unauthorized` | missing/invalid token |
| 403 | `security_violation` | path outside approved roots, sensitive directory, unsafe patch |
| 404 | `not_found` | unknown id |
| 409 | `conflict` | duplicate active run, invalid state transition (e.g. reviewing twice) |
| 422 | `validation_failed` | malformed input, unknown revision, out-of-bounds setting |
| 503 | `service_unavailable` | test runner unavailable |

Send `X-Correlation-ID` (8–64 characters `[A-Za-z0-9-]`) to tag the logs, traces, and
any runs a request creates. The response echoes it, or a generated one if you didn't
send one.

## Endpoints

### System
| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Status, DB, runner availability, active jobs, warnings (no auth) |
| GET | `/metrics` | Prometheus metrics (no auth; read-only, no sensitive data) |
| GET | `/api/settings` | Runtime settings + read-only process configuration |
| PUT | `/api/settings` | Update runtime settings (bounded; see SECURITY.md) |

### Repositories
| Method | Path | Description |
|---|---|---|
| GET | `/api/repositories` | List registered repositories |
| POST | `/api/repositories` | Register `{path, name?}` (201) |
| POST | `/api/repositories/examples` | Install + register the bundled examples (201) |
| GET | `/api/repositories/{id}` | Details + branch, HEAD, branches, recent commits |
| GET | `/api/repositories/{id}/commits?rev=&limit=` | Commit log |
| POST | `/api/repositories/{id}/refresh` | Re-detect languages/structure |
| DELETE | `/api/repositories/{id}` | Unregister (204). Files on disk are untouched |

### Runs
| Method | Path | Description |
|---|---|---|
| POST | `/api/runs` | Start `{repository_id, revision="HEAD"}` (202; 409 if identical run active) |
| GET | `/api/runs?repository_id=&status=&q=&limit=&offset=` | Paginated, searchable history |
| GET | `/api/runs/{id}` | Run status, counts, timings |
| POST | `/api/runs/{id}/cancel` | Cancel a queued/running run (409 if finished) |
| GET | `/api/runs/{id}/results` | Per-test results |
| GET | `/api/runs/{id}/failures` | Classified failures with tracebacks and occurrence counts |
| GET | `/api/runs/{id}/logs?stream=stdout\|stderr` | Captured output (last 256 KB, redacted) |
| GET | `/api/runs/{id}/compare?baseline=` | Fixed / regressed / still failing vs a baseline |

### Analysis and patches
| Method | Path | Description |
|---|---|---|
| GET | `/api/providers` | Provider availability |
| POST | `/api/analyses` | Analyze a failed run `{run_id, provider?}` (202) |
| GET | `/api/analyses?run_id=` / `/api/analyses/{id}` | Diagnoses (+ `patch_ids`) |
| GET | `/api/patches?run_id=&status=` / `/api/patches/{id}` | Proposals with risk and decisions |
| POST | `/api/patches/{id}/review` | `{decision: approve\|reject, comment?}`. Approve starts a sandboxed retest |
| POST | `/api/patches/{id}/retest` | Retest an approved patch again |
| POST | `/api/patches/{id}/export` | `{confirm: true, branch?}`. Writes a new branch (retest must have passed) |

### Metrics
| Method | Path | Description |
|---|---|---|
| GET | `/api/metrics/overview` | Totals, pass rate, durations, productivity, AI acceptance, recent runs |
| GET | `/api/metrics/timeseries?days=30` | Daily series + per-run timings |
| GET | `/api/benchmarks` | Benchmark results grouped by batch/scenario/mode |

## Example session

```bash
TOKEN=$(devflow token); H="Authorization: Bearer $TOKEN"
curl -s -X POST -H "$H" localhost:8000/api/repositories/examples | jq '.[] | {id, name}'
curl -s -X POST -H "$H" -H 'Content-Type: application/json' \
     -d '{"repository_id": 2}' localhost:8000/api/runs | jq '{id, status}'
curl -s -H "$H" localhost:8000/api/runs/1/failures | jq '.[] | {category, nodeid, summary}'
curl -s -X POST -H "$H" -H 'Content-Type: application/json' \
     -d '{"run_id": 1, "provider": "heuristic"}' localhost:8000/api/analyses | jq '{id, status}'
```
