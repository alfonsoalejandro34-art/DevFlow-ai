import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { Card, Empty, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { ACTIVE_STATUSES, formatMs, formatTime, shortSha } from "../services/format";
import type { Repository, RunPage } from "../services/types";
import { useApi } from "../services/useApi";

const PAGE_SIZE = 25;
const STATUSES = ["queued", "running", "passed", "failed", "error", "timeout", "cancelled"];

export function BuildsPage() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") ?? "");
  const status = params.get("status") ?? "";
  const repository = params.get("repository") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const repos = useApi<Repository[]>("/api/repositories");
  const navigate = useNavigate();

  // Debounce the free-text search into the URL.
  useEffect(() => {
    const id = window.setTimeout(() => update({ q: query, offset: "" }), 300);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  function update(values: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(values)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }

  const search = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (status) search.set("status", status);
  if (repository) search.set("repository_id", repository);
  if (params.get("q")) search.set("q", params.get("q")!);
  const runs = useApi<RunPage>(`/api/runs?${search}`, 3000);
  const page = runs.data;
  const hasActive = page?.items.some((r) => ACTIVE_STATUSES.has(r.status));

  return (
    <div className="stack">
      <PageHeader title="Build history" description="Every test run, newest first. Active runs refresh automatically." />
      <div className="row">
        <input className="input" style={{ flex: "1 1 260px" }} placeholder="Search repository, commit, or error…"
          value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search runs" />
        <select className="select" value={repository} onChange={(e) => update({ repository: e.target.value, offset: "" })} aria-label="Repository filter">
          <option value="">All repositories</option>
          {repos.data?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <select className="select" value={status} onChange={(e) => update({ status: e.target.value, offset: "" })} aria-label="Status filter">
          <option value="">Any status</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {hasActive && <StatusBadge status="running" label="live" />}
      </div>
      <ErrorBanner error={runs.error} />
      <Card bodyClass="table-wrap">
        {!page ? <Loading /> : page.items.length === 0 ? (
          <Empty title="No matching runs" />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Run</th><th>Started</th><th>Repository</th><th>Commit</th><th>Status</th>
                <th className="num">Duration</th><th className="num">Passed</th><th className="num">Failed</th><th>Runner</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                  <td><Link to={`/runs/${r.id}`}>#{r.id}</Link>{r.kind === "patch_retest" && <span className="chip" style={{ marginLeft: 6 }}>patch #{r.patch_id}</span>}</td>
                  <td className="secondary">{formatTime(r.created_at)}</td>
                  <td>{r.repository_name}</td>
                  <td className="mono" title={r.revision?.message}>{shortSha(r.source_info.sha)}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td className="num">{formatMs(r.duration_ms)}</td>
                  <td className="num">{r.passed}</td>
                  <td className="num">{r.failed + r.errors}</td>
                  <td className="secondary">{r.runner}{r.sandboxed ? "" : " ⚠"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {page && page.total > PAGE_SIZE && (
        <div className="spread">
          <span className="muted">{offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}</span>
          <div className="row">
            <button className="btn btn-sm" disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}>Previous</button>
            <button className="btn btn-sm" disabled={offset + PAGE_SIZE >= page.total} onClick={() => update({ offset: String(offset + PAGE_SIZE) })}>Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
