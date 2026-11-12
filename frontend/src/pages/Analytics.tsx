import { useState } from "react";
import { HorizontalBars, StackedBars, TrendLine } from "../charts/Charts";
import { Card, Empty, ErrorBanner, Loading, PageHeader, Stat } from "../components/ui";
import { formatMs, formatPercent } from "../services/format";
import type { BenchmarkRow, Overview, Timeseries } from "../services/types";
import { useApi } from "../services/useApi";

const RANGES = [7, 30, 90];
const CATEGORY_LABELS: Record<string, string> = {
  assertion: "Assertion",
  exception: "Exception",
  syntax_error: "Syntax error",
  missing_dependency: "Missing dependency",
  timeout: "Timeout",
  collection_error: "Collection error",
  infrastructure: "Infrastructure",
};

export function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const [showTable, setShowTable] = useState(false);
  const series = useApi<Timeseries>(`/api/metrics/timeseries?days=${days}`, 15000);
  const overview = useApi<Overview>("/api/metrics/overview", 15000);
  const benchmarks = useApi<BenchmarkRow[]>("/api/benchmarks");
  const t = series.data;
  const o = overview.data;

  const daily = t?.daily ?? [];
  const failureData = Object.entries(o?.productivity.failure_categories ?? {})
    .map(([k, v]) => ({ label: CATEGORY_LABELS[k] ?? k, value: v }))
    .sort((a, b) => b.value - a.value);
  const perRun = (t?.runs ?? [])
    .filter((r) => r.test_duration_ms !== null && r.overhead_ms !== null)
    .map((r) => ({ id: r.id, test: (r.test_duration_ms ?? 0) / 1000, overhead: (r.overhead_ms ?? 0) / 1000 }));
  // Per-run series stay informative even when all activity falls on a single day.
  const finished = (t?.runs ?? []).filter((r) => r.status === "passed" || r.status === "failed" || r.status === "error" || r.status === "timeout");
  const perRunSeries = finished.map((r, i) => {
    const window = finished.slice(Math.max(0, i - 9), i + 1);
    return {
      id: r.id,
      duration: r.duration_ms,
      failing: r.failed,
      rolling_pass_rate: Math.round((100 * window.filter((w) => w.status === "passed").length) / window.length),
    };
  });

  return (
    <div className="stack">
      <PageHeader
        title="Performance analytics"
        description="All figures are computed from recorded runs, failures, and review decisions. Nothing here is estimated."
        actions={
          <div className="segmented" role="group" aria-label="Time range">
            {RANGES.map((r) => <button key={r} className={days === r ? "active" : ""} onClick={() => setDays(r)}>{r}d</button>)}
          </div>
        }
      />
      <ErrorBanner error={series.error ?? overview.error} />
      {!t || !o ? <Loading /> : (
        <>
          <div className="grid grid-stats">
            <Stat label="Pass rate (all time)" value={formatPercent(o.runs.pass_rate)} />
            <Stat label="Median build time" value={formatMs(o.runs.median_duration_ms)} />
            <Stat label="Avg time to failure signal" value={formatMs(o.productivity.avg_time_to_failure_ms)} />
            <Stat label="Avg diagnosis time" value={formatMs(o.productivity.avg_diagnosis_ms)} hint="analysis request → result" />
            <Stat label="Avg time to validate fix" value={formatMs(o.productivity.avg_time_to_validate_fix_ms)} />
            <Stat label="Suggestion acceptance" value={formatPercent(o.ai.acceptance_rate)} hint={`${o.ai.approved} approved · ${o.ai.rejected} rejected`} />
          </div>

          {daily.length === 0 ? (
            <Card><Empty title={`No runs in the last ${days} days`} /></Card>
          ) : (
            <div className="grid grid-2">
              <Card title="Build history" subtitle="Completed runs per day by outcome">
                <StackedBars data={daily as never} format={(v) => String(v)} series={[
                  { key: "passed", label: "Passed", color: "var(--good)" },
                  { key: "failed", label: "Failed / error / timeout", color: "var(--critical)" },
                ]} />
              </Card>
              <Card title="Test pass rate" subtitle="Rolling share of passing runs over the previous 10 runs">
                <TrendLine data={perRunSeries as never} xKey="id" format={(v) => `${v}%`} domain={[0, 100]}
                  series={[{ key: "rolling_pass_rate", label: "Pass rate", color: "var(--series-1)" }]} />
              </Card>
              <Card title="Build duration" subtitle="Submission to result, per run">
                <TrendLine data={perRunSeries as never} xKey="id" format={(v) => formatMs(v)}
                  series={[{ key: "duration", label: "Duration", color: "var(--series-1)" }]} />
              </Card>
              <Card title="Failure trend" subtitle="Failing tests per run">
                <TrendLine data={perRunSeries as never} xKey="id" format={(v) => String(v)}
                  series={[{ key: "failing", label: "Failing tests", color: "var(--series-2)" }]} />
              </Card>
            </div>
          )}

          <div className="grid grid-2">
            <Card title="Failures by category" subtitle="All recorded failure records">
              {failureData.length ? <HorizontalBars data={failureData} color="var(--series-2)" format={(v) => String(v)} /> : <Empty title="No failures recorded" />}
            </Card>
            <Card title="Where run time goes" subtitle="Per run: time inside pytest vs runner overhead (seconds)">
              {perRun.length ? (
                <StackedBars data={perRun as never} xKey="id" format={(v) => `${v.toFixed(1)}s`} series={[
                  { key: "test", label: "pytest", color: "var(--series-1)" },
                  { key: "overhead", label: "Runner overhead", color: "var(--series-4)" },
                ]} />
              ) : <Empty title="No timing data yet" />}
            </Card>
          </div>

          <Card title="AI suggestion acceptance" subtitle="Share of reviewed patch proposals that were approved, per day of decision">
            {daily.some((d) => d.acceptance_rate !== null) ? (
              <TrendLine data={daily as never} format={(v) => `${v}%`} domain={[0, 100]}
                series={[{ key: "acceptance_rate", label: "Acceptance rate", color: "var(--series-3)" }]} height={180} />
            ) : <Empty title="No review decisions yet" />}
          </Card>

          <Card title="Daily data" actions={<button className="btn btn-sm" onClick={() => setShowTable(!showTable)}>{showTable ? "Hide table" : "Show table"}</button>} bodyClass="table-wrap">
            {showTable ? (
              <table className="table">
                <thead><tr><th>Date</th><th className="num">Runs</th><th className="num">Passed</th><th className="num">Failed</th><th className="num">Pass rate</th><th className="num">Avg duration</th><th className="num">Acceptance</th></tr></thead>
                <tbody>
                  {daily.map((d) => (
                    <tr key={d.date}>
                      <td>{d.date}</td><td className="num">{d.runs}</td><td className="num">{d.passed}</td><td className="num">{d.failed}</td>
                      <td className="num">{formatPercent(d.pass_rate)}</td><td className="num">{formatMs(d.avg_duration_ms)}</td><td className="num">{formatPercent(d.acceptance_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="muted small" style={{ margin: 0, padding: "4px 16px 16px" }}>Accessible tabular view of the daily figures.</p>}
          </Card>

          <Card title="Benchmarks" subtitle="Recorded by benchmarks/run_benchmarks.py: baseline (direct pytest) vs DevFlow workflow" bodyClass="table-wrap">
            {!benchmarks.data?.length ? (
              <Empty title="No benchmark results recorded">Run <code>make benchmark</code> to record them.</Empty>
            ) : (
              <table className="table">
                <thead><tr><th>Scenario</th><th>Mode</th><th className="num">n</th><th className="num">Median</th><th className="num">p90</th><th className="num">Min</th><th className="num">Max</th><th className="num">Std dev</th></tr></thead>
                <tbody>
                  {benchmarks.data.map((b) => (
                    <tr key={`${b.batch_id}-${b.scenario}-${b.mode}`}>
                      <td>{b.scenario}</td><td>{b.mode}</td><td className="num">{b.successes}/{b.iterations}</td>
                      <td className="num">{formatMs(b.median_ms)}</td><td className="num">{formatMs(b.p90_ms)}</td>
                      <td className="num">{formatMs(b.min_ms)}</td><td className="num">{formatMs(b.max_ms)}</td><td className="num">{formatMs(b.stdev_ms)}</td>
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
