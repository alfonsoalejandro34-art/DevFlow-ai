"""Developer-productivity metrics computed from recorded runs, failures, and reviews.

Every number here is derived from rows in the database; nothing is estimated or
synthesized. Metrics with no underlying data are returned as ``None``.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from devflow.database.models import (
    ACTIVE_STATUSES,
    AnalysisRequest,
    BenchmarkResult,
    Failure,
    PatchProposal,
    Repository,
    TestRun,
    utcnow,
)
from devflow.database.session import Database

_FAILED = ("failed", "error", "timeout")


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 1) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


class AnalyticsService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def overview(self) -> dict[str, Any]:
        with self.db.session() as session:
            runs = list(session.scalars(select(TestRun)))
            names = dict(session.execute(select(Repository.id, Repository.name)).all())
            analyses = list(session.scalars(select(AnalysisRequest)))
            patches = list(session.scalars(select(PatchProposal)))
            failures = list(session.scalars(select(Failure)))
            repeated = session.scalar(
                select(func.count()).select_from(
                    select(Failure.signature)
                    .group_by(Failure.signature)
                    .having(func.count(func.distinct(Failure.run_id)) > 1)
                    .subquery()
                )
            )
        runs_by_id = {r.id: r for r in runs}
        done = [r for r in runs if r.status not in ACTIVE_STATUSES and r.status != "cancelled"]
        verdict = [r for r in done if r.status in ("passed", *_FAILED)]
        failed = [r for r in done if r.status in _FAILED]
        completed_ok = [
            r for r in done if r.status in ("passed", "failed") and r.duration_ms is not None
        ]

        reviewed = [p for p in patches if p.decided_at is not None]
        approved = [p for p in reviewed if p.status not in ("rejected",)]
        ai_reviewed = [p for p in reviewed if p.analysis_id in {a.id for a in analyses if a.is_ai}]
        ai_approved = [p for p in ai_reviewed if p.status != "rejected"]
        validate_ms = [
            (runs_by_id[p.retest_run_id].finished_at - p.decided_at).total_seconds() * 1000  # type: ignore[operator]
            for p in patches
            if p.retest_run_id in runs_by_id
            and runs_by_id[p.retest_run_id].finished_at
            and p.decided_at
        ]
        analysis_ms = [
            float(a.duration_ms) for a in analyses if a.status == "completed" and a.duration_ms
        ]

        recent = sorted(runs, key=lambda r: r.created_at, reverse=True)[:8]
        return {
            "runs": {
                "total": len(runs),
                "active": sum(r.status in ACTIVE_STATUSES for r in runs),
                "completed": len(done),
                "passed": sum(r.status == "passed" for r in done),
                "failed_builds": len(failed),
                "cancelled": sum(r.status == "cancelled" for r in runs),
                "pass_rate": round(
                    100 * sum(r.status == "passed" for r in verdict) / len(verdict), 1
                )
                if verdict
                else None,
                "avg_duration_ms": _mean([float(r.duration_ms) for r in completed_ok]),  # type: ignore[arg-type]
                "median_duration_ms": _median([float(r.duration_ms) for r in completed_ok]),  # type: ignore[arg-type]
                "sandboxed_share": round(100 * sum(r.sandboxed for r in runs) / len(runs), 1)
                if runs
                else None,
            },
            "productivity": {
                "avg_time_to_failure_ms": _mean(
                    [float(r.duration_ms) for r in failed if r.duration_ms]
                ),
                "avg_time_to_validate_fix_ms": _mean(validate_ms),
                "avg_diagnosis_ms": _mean(analysis_ms),
                "avg_runner_overhead_ms": _mean(
                    [float(r.overhead_ms) for r in done if r.overhead_ms is not None]
                ),
                "repeated_failures": repeated or 0,
                "resolved_failures": sum(f.resolved_by_run_id is not None for f in failures),
                "open_failures": sum(f.resolved_by_run_id is None for f in failures),
                "failure_categories": dict(Counter(f.category for f in failures)),
            },
            "ai": {
                "analyses": len(analyses),
                "ai_analyses": sum(a.is_ai for a in analyses),
                "by_provider": dict(Counter(a.provider for a in analyses)),
                "by_status": dict(Counter(a.status for a in analyses)),
                "proposals": len(patches),
                "invalid_proposals": sum(p.status == "invalid" for p in patches),
                "approved": len(approved),
                "rejected": sum(p.status == "rejected" for p in reviewed),
                "acceptance_rate": round(100 * len(approved) / len(reviewed), 1)
                if reviewed
                else None,
                "ai_acceptance_rate": round(100 * len(ai_approved) / len(ai_reviewed), 1)
                if ai_reviewed
                else None,
                "retest_passed": sum(p.status in ("retest_passed", "exported") for p in patches),
                "retest_failed": sum(p.status == "retest_failed" for p in patches),
            },
            "recent_runs": [
                {
                    "id": r.id,
                    "repository": names.get(r.repository_id),
                    "status": r.status,
                    "kind": r.kind,
                    "created_at": r.created_at,
                    "passed": r.passed,
                    "failed": r.failed + r.errors,
                    "duration_ms": r.duration_ms,
                }
                for r in recent
            ],
        }

    def timeseries(self, days: int = 30) -> dict[str, Any]:
        since = utcnow() - timedelta(days=days)
        with self.db.session() as session:
            runs = list(
                session.scalars(
                    select(TestRun).where(TestRun.created_at >= since).order_by(TestRun.id)
                )
            )
            failures = list(session.scalars(select(Failure).where(Failure.created_at >= since)))
            patches = list(
                session.scalars(select(PatchProposal).where(PatchProposal.created_at >= since))
            )

        daily: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "runs": 0,
                "passed": 0,
                "failed": 0,
                "durations": [],
                "failures": Counter(),
                "approved": 0,
                "rejected": 0,
            }
        )
        for run in runs:
            if run.status in ACTIVE_STATUSES:
                continue
            day = daily[run.created_at.date().isoformat()]
            day["runs"] += 1
            day["passed"] += run.status == "passed"
            day["failed"] += run.status in _FAILED
            if run.duration_ms is not None and run.status in ("passed", "failed"):
                day["durations"].append(run.duration_ms)
        for failure in failures:
            daily[failure.created_at.date().isoformat()]["failures"][failure.category] += 1
        for patch in patches:
            if patch.decided_at:
                bucket = daily[patch.decided_at.date().isoformat()]
                bucket["rejected" if patch.status == "rejected" else "approved"] += 1

        series = []
        for date in sorted(daily):
            d = daily[date]
            verdicts = d["passed"] + d["failed"]
            reviewed = d["approved"] + d["rejected"]
            series.append(
                {
                    "date": date,
                    "runs": d["runs"],
                    "passed": d["passed"],
                    "failed": d["failed"],
                    "pass_rate": round(100 * d["passed"] / verdicts, 1) if verdicts else None,
                    "avg_duration_ms": _mean(d["durations"]),
                    "failures_by_category": dict(d["failures"]),
                    "acceptance_rate": round(100 * d["approved"] / reviewed, 1)
                    if reviewed
                    else None,
                }
            )
        per_run = [
            {
                "id": r.id,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "test_duration_ms": r.test_duration_ms,
                "overhead_ms": r.overhead_ms,
                "passed": r.passed,
                "failed": r.failed + r.errors,
            }
            for r in runs
            if r.status not in ACTIVE_STATUSES
        ][-60:]
        return {"days": days, "daily": series, "runs": per_run}

    def benchmarks(self) -> list[dict[str, Any]]:
        with self.db.session() as session:
            rows = list(session.scalars(select(BenchmarkResult).order_by(BenchmarkResult.id)))
        grouped: dict[tuple[str, str, str], list[BenchmarkResult]] = defaultdict(list)
        for row in rows:
            grouped[(row.batch_id, row.scenario, row.mode)].append(row)
        out = []
        for (batch, scenario, mode), items in grouped.items():
            durations = sorted(i.duration_ms for i in items if i.success)
            out.append(
                {
                    "batch_id": batch,
                    "scenario": scenario,
                    "mode": mode,
                    "iterations": len(items),
                    "successes": len(durations),
                    "median_ms": _median(durations),
                    "p90_ms": round(
                        durations[min(len(durations) - 1, int(0.9 * len(durations)))], 1
                    )
                    if durations
                    else None,
                    "min_ms": durations[0] if durations else None,
                    "max_ms": durations[-1] if durations else None,
                    "stdev_ms": round(statistics.stdev(durations), 1)
                    if len(durations) > 1
                    else None,
                    "created_at": items[0].created_at,
                }
            )
        return out
