import { Link, useNavigate } from "react-router-dom";
import { TrendLine } from "../charts/Charts";
import { StatusBadge } from "../components/StatusBadge";
import { Card, Empty, ErrorBanner, Loading, PageHeader, Stat } from "../components/ui";
import { formatMs, formatPercent, relativeTime } from "../services/format";
import type { Overview, Timeseries } from "../services/types";
import { useApi } from "../services/useApi";

export function OverviewPage() {
  const overview = useApi<Overview>("/api/metrics/overview", 5000);
  const series = useApi<Timeseries>("/api/metrics/timeseries?days=30", 15000);
  const navigate = useNavigate();
  const o = overview.data;

  return (
    <div className="stack">
      <PageHeader title="Overview" description="Test activity, reliability, and failure-analysis outcomes across registered repositories." />
      <ErrorBanner error={overview.error} />
      {!o ? (
        <Loading />
      ) : (
        <>
          <div className="grid grid-stats">
            <Stat label="Total test runs" value={o.runs.total} hint={`${o.runs.active} active · ${o.runs.cancelled} cancelled`} />
            <Stat label="Pass rate" value={formatPercent(o.runs.pass_rate)} hint={`${o.runs.passed} of ${o.runs.passed + o.runs.failed_builds} runs with a verdict`} />
            <Stat label="Avg build time" value={formatMs(o.runs.avg_duration_ms)} hint={`median ${formatMs(o.runs.median_duration_ms)}`} />
            <Stat label="Failed builds" value={o.runs.failed_builds} hint={`${o.productivity.open_failures} open failure records`} />
            <Stat label="Resolved failures" value={o.productivity.resolved_failures} hint={`${o.productivity.repeated_failures} repeated signatures`} />
            <Stat label="Sandboxed runs" value={formatPercent(o.runs.sandboxed_share)} hint="executed in Docker" />
          </div>

          <div className="grid grid-2">
            <Card title="Pass rate" subtitle="Share of runs that passed, per day (last 30 days)">
              {series.data && series.data.daily.some((d) => d.pass_rate !== null) ? (
                <TrendLine data={series.data.daily as never} format={(v) => `${v}%`} domain={[0, 100]}
                  series={[{ key: "pass_rate", label: "Pass rate", color: "var(--series-1)" }]} height={200} />
              ) : (
                <Empty title="No completed runs yet" />
              )}
            </Card>
            <Card title="Failure analysis" subtitle="Diagnoses, proposals, and human review decisions">
              <dl className="kv">
                <dt>Analyses</dt>
                <dd>{o.ai.analyses} total · {o.ai.ai_analyses} by an AI provider</dd>
                <dt>By provider</dt>
                <dd>{Object.entries(o.ai.by_provider).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</dd>
                <dt>Patch proposals</dt>
                <dd>{o.ai.proposals} · {o.ai.invalid_proposals} rejected by validation</dd>
                <dt>Review decisions</dt>
                <dd>{o.ai.approved} approved · {o.ai.rejected} rejected</dd>
                <dt>Acceptance rate</dt>
                <dd>{formatPercent(o.ai.acceptance_rate)} overall · {formatPercent(o.ai.ai_acceptance_rate)} for AI proposals</dd>
                <dt>Retest outcome</dt>
                <dd>{o.ai.retest_passed} passed · {o.ai.retest_failed} still failing</dd>
                <dt>Avg diagnosis time</dt>
                <dd>{formatMs(o.productivity.avg_diagnosis_ms)}</dd>
              </dl>
            </Card>
          </div>

          <div className="grid grid-stats">
            <Stat label="Time to failure signal" value={formatMs(o.productivity.avg_time_to_failure_ms)} hint="submission → classified failures" />
            <Stat label="Time to validate a fix" value={formatMs(o.productivity.avg_time_to_validate_fix_ms)} hint="approval → retest verdict" />
            <Stat label="Runner overhead" value={formatMs(o.productivity.avg_runner_overhead_ms)} hint="wall time outside pytest" />
          </div>

          <Card title="Recent activity" actions={<Link className="btn btn-sm" to="/builds">All builds</Link>} bodyClass="table-wrap">
            {o.recent_runs.length === 0 ? (
              <Empty title="No runs yet">
                Go to <Link to="/repositories">Repositories</Link> to install the examples and start a run.
              </Empty>
            ) : (
              <table className="table">
                <thead>
                  <tr><th>Run</th><th>Repository</th><th>Status</th><th>Type</th><th className="num">Passed</th><th className="num">Failed</th><th className="num">Duration</th><th>When</th></tr>
                </thead>
                <tbody>
                  {o.recent_runs.map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                      <td><Link to={`/runs/${r.id}`}>#{r.id}</Link></td>
                      <td>{r.repository}</td>
                      <td><StatusBadge status={r.status} /></td>
                      <td className="secondary">{r.kind === "patch_retest" ? "patch retest" : "revision"}</td>
                      <td className="num">{r.passed}</td>
                      <td className="num">{r.failed}</td>
                      <td className="num">{formatMs(r.duration_ms)}</td>
                      <td className="secondary">{relativeTime(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
