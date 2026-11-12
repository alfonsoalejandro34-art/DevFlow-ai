import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CodeBlock } from "../components/CodeViews";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { Banner, Card, Empty, ErrorBanner, Loading, Stat } from "../components/ui";
import { post } from "../services/api";
import { ACTIVE_STATUSES, formatMs, formatTime, shortSha } from "../services/format";
import type { Comparison, Failure, Run, TestResult } from "../services/types";
import { useApi } from "../services/useApi";
import { AnalysisPanel } from "./AnalysisPanel";

type Tab = "failures" | "tests" | "logs" | "analysis" | "compare";

export function RunDetailPage() {
  const runId = Number(useParams().runId);
  const [tab, setTab] = useState<Tab>("failures");
  const [actionError, setActionError] = useState<Error>();
  const run = useApi<Run>(`/api/runs/${runId}`, 2000);
  const r = run.data;
  const active = r ? ACTIVE_STATUSES.has(r.status) : true;

  async function cancel() {
    try {
      await post(`/api/runs/${runId}/cancel`);
      await run.reload();
    } catch (err) {
      setActionError(err as Error);
    }
  }

  if (run.error) return <ErrorBanner error={run.error} />;
  if (!r) return <Loading />;
  const failing = r.failed + r.errors;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <div className="row">
            <h1>Run #{r.id}</h1>
            <StatusBadge status={r.status} />
            {r.kind === "patch_retest" && <span className="chip">retest of patch #{r.patch_id}</span>}
            {r.kind === "ai_verification" && <span className="chip">AI candidate check (not a build)</span>}
          </div>
          <p>
            <Link to={`/builds?repository=${r.repository_id}`}>{r.repository_name}</Link>
            {" · "}<span className="mono">{shortSha(r.source_info.sha)}</span>
            {r.revision?.message && <> · {r.revision.message}</>}
            {r.parent_run_id && <> · baseline <Link to={`/runs/${r.parent_run_id}`}>#{r.parent_run_id}</Link></>}
          </p>
        </div>
        <div className="row">
          {active && (
            <button className="btn btn-danger" onClick={() => void cancel()} disabled={r.cancel_requested}>
              <Icon name="stop" size={14} /> {r.cancel_requested ? "Cancelling…" : "Cancel run"}
            </button>
          )}
        </div>
      </div>

      <ErrorBanner error={actionError} />
      {!r.sandboxed && (
        <Banner tone="warning">This run used the unsandboxed local runner: the tests executed directly on the host.</Banner>
      )}
      {r.error_message && r.status !== "failed" && (
        <Banner tone={r.status === "cancelled" ? "info" : "error"}>{r.error_message}</Banner>
      )}

      <div className="grid grid-stats">
        <Stat label="Tests" value={r.total} hint={`${r.passed} passed · ${failing} failed · ${r.skipped} skipped`} />
        <Stat label="Total duration" value={formatMs(r.duration_ms)} hint="submission → result" />
        <Stat label="pytest time" value={formatMs(r.test_duration_ms)} hint={`runner overhead ${formatMs(r.overhead_ms)}`} />
        <Stat label="Runner" value={r.runner} hint={r.sandboxed ? "Docker sandbox, network disabled" : "no isolation"} />
      </div>

      <dl className="kv card card-body">
        <dt>Queued</dt><dd>{formatTime(r.created_at)}</dd>
        <dt>Started</dt><dd>{formatTime(r.started_at)}</dd>
        <dt>Finished</dt><dd>{formatTime(r.finished_at)}</dd>
        <dt>Exit code</dt><dd className="mono">{r.exit_code ?? "—"}</dd>
        <dt>Commit</dt><dd className="mono">{r.source_info.sha}</dd>
        <dt>Correlation ID</dt><dd className="mono">{r.correlation_id ?? "—"}</dd>
      </dl>

      <div className="tabs" role="tablist">
        {(["failures", "tests", "logs", "analysis", "compare"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
            {t === "failures" ? `Failures (${failing})` : t === "tests" ? `Tests (${r.total})` : t === "analysis" ? "Diagnosis & patches" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === "failures" && <FailuresTab runId={r.id} active={active} />}
      {tab === "tests" && <TestsTab runId={r.id} active={active} />}
      {tab === "logs" && <LogsTab runId={r.id} active={active} />}
      {tab === "analysis" && <AnalysisPanel run={r} />}
      {tab === "compare" && <CompareTab runId={r.id} active={active} />}
    </div>
  );
}

const CATEGORY_LABELS: Record<string, string> = {
  assertion: "Assertion",
  exception: "Exception",
  syntax_error: "Syntax error",
  missing_dependency: "Missing dependency",
  timeout: "Timeout",
  collection_error: "Collection error",
  infrastructure: "Infrastructure",
};

function FailuresTab({ runId, active }: { runId: number; active: boolean }) {
  const failures = useApi<Failure[]>(`/api/runs/${runId}/failures`, active && 2000);
  if (failures.error) return <ErrorBanner error={failures.error} />;
  if (!failures.data) return <Loading />;
  if (!failures.data.length) return <Empty title={active ? "Run in progress" : "No failures"}>{active ? "Failures appear here as soon as the run finishes." : "Every collected test passed."}</Empty>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      {failures.data.map((f) => (
        <details key={f.id} className="failure">
          <summary>
            <div className="spread">
              <div className="row">
                <span className="badge badge-bad">{CATEGORY_LABELS[f.category] ?? f.category}</span>
                <span className="mono">{f.nodeid ?? "run-level error"}</span>
              </div>
              <div className="row small muted">
                {f.occurrences > 1 && <span className="chip" title="Same failure signature in other runs">seen in {f.occurrences} runs</span>}
                {f.resolved_by_run_id && <Link to={`/runs/${f.resolved_by_run_id}`} onClick={(e) => e.stopPropagation()}><StatusBadge status="passed" label={`resolved in #${f.resolved_by_run_id}`} /></Link>}
              </div>
            </div>
            <div className="secondary" style={{ marginTop: 6 }}>{f.summary}</div>
          </summary>
          <div className="failure-body stack" style={{ gap: 10 }}>
            <dl className="kv">
              <dt>Exception</dt><dd className="mono">{f.exception_type ?? "—"}</dd>
              <dt>Location</dt><dd className="mono">{f.file ? `${f.file}:${f.line ?? "?"}` : "—"}</dd>
              <dt>Signature</dt><dd className="mono">{f.signature.slice(0, 16)}</dd>
            </dl>
            {f.traceback && <CodeBlock text={f.traceback} label="Traceback" />}
          </div>
        </details>
      ))}
    </div>
  );
}

function TestsTab({ runId, active }: { runId: number; active: boolean }) {
  const results = useApi<TestResult[]>(`/api/runs/${runId}/results`, active && 2000);
  const [filter, setFilter] = useState<"all" | "failing">("all");
  if (results.error) return <ErrorBanner error={results.error} />;
  if (!results.data) return <Loading />;
  const rows = results.data.filter((t) => filter === "all" || t.outcome === "failed" || t.outcome === "error");
  return (
    <Card actions={
      <div className="segmented">
        <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>All</button>
        <button className={filter === "failing" ? "active" : ""} onClick={() => setFilter("failing")}>Failing</button>
      </div>
    } bodyClass="table-wrap">
      {!rows.length ? <Empty title="No test results" /> : (
        <table className="table">
          <thead><tr><th>Test</th><th>Outcome</th><th className="num">Duration</th></tr></thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td className="mono">{t.nodeid}</td>
                <td><StatusBadge status={t.outcome === "error" ? "error" : t.outcome} /></td>
                <td className="num">{formatMs(t.duration_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function LogsTab({ runId, active }: { runId: number; active: boolean }) {
  const [stream, setStream] = useState<"stdout" | "stderr">("stdout");
  const logs = useApi<{ content: string }>(`/api/runs/${runId}/logs?stream=${stream}`, active && 2000);
  return (
    <Card
      title="Captured output"
      subtitle="Redacted for known secret formats; last 256 KB shown."
      actions={
        <div className="segmented">
          <button className={stream === "stdout" ? "active" : ""} onClick={() => setStream("stdout")}>stdout</button>
          <button className={stream === "stderr" ? "active" : ""} onClick={() => setStream("stderr")}>stderr</button>
        </div>
      }
    >
      <ErrorBanner error={logs.error} />
      {logs.data ? <CodeBlock text={logs.data.content} label={`${stream} log`} /> : <Loading />}
    </Card>
  );
}

function CompareTab({ runId, active }: { runId: number; active: boolean }) {
  const comparison = useApi<Comparison>(active ? null : `/api/runs/${runId}/compare`);
  if (active) return <Empty title="Run in progress">Comparison is available when the run finishes.</Empty>;
  if (comparison.error) return <Empty title="Nothing to compare">{comparison.error.message}</Empty>;
  if (!comparison.data) return <Loading />;
  const c = comparison.data;
  const groups: [string, string[], string][] = [
    ["Fixed", c.fixed, "passed"],
    ["Regressed", c.regressed, "failed"],
    ["Still failing", c.still_failing, "error"],
    ["New tests", c.added, "approved"],
    ["Removed tests", c.removed, "cancelled"],
  ];
  return (
    <Card title={<>Compared with baseline <Link to={`/runs/${c.baseline_run_id}`}>#{c.baseline_run_id}</Link></>}>
      <div className="grid grid-2">
        {groups.map(([label, items, tone]) => (
          <div key={label}>
            <div className="row" style={{ marginBottom: 6 }}><StatusBadge status={tone} label={label} /><span className="muted">{items.length}</span></div>
            {items.length ? <ul className="mono small" style={{ margin: 0, paddingLeft: 18 }}>{items.map((n) => <li key={n}>{n}</li>)}</ul> : <span className="muted small">none</span>}
          </div>
        ))}
      </div>
    </Card>
  );
}
