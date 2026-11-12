"""Prometheus metrics, exposed at ``GET /metrics``."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_LATENCY = Histogram(
    "devflow_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route", "status"],
    registry=REGISTRY,
)
RUNS_TOTAL = Counter(
    "devflow_test_runs_total", "Completed test runs", ["status", "runner"], registry=REGISTRY
)
RUN_DURATION = Histogram(
    "devflow_test_run_duration_seconds",
    "Wall-clock test run duration",
    ["runner"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)
RUNNER_OVERHEAD = Histogram(
    "devflow_runner_overhead_seconds",
    "Run wall time not spent inside pytest (container start, teardown, snapshot)",
    ["runner"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10),
    registry=REGISTRY,
)
ACTIVE_JOBS = Gauge("devflow_active_jobs", "Jobs currently queued or running", registry=REGISTRY)
TESTS_TOTAL = Counter(
    "devflow_test_results_total", "Individual test outcomes", ["outcome"], registry=REGISTRY
)
ANALYSES_TOTAL = Counter(
    "devflow_analyses_total", "Failure analyses", ["provider", "status"], registry=REGISTRY
)
PATCH_DECISIONS = Counter(
    "devflow_patch_decisions_total", "Human review decisions", ["decision"], registry=REGISTRY
)
JOB_ERRORS = Counter(
    "devflow_job_errors_total", "Jobs that ended in an infrastructure error", registry=REGISTRY
)
