import { useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { Card, Empty, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { formatTime } from "../services/format";
import type { Patch } from "../services/types";
import { useApi } from "../services/useApi";
import { PatchReview } from "./PatchReview";

const FILTERS = ["", "proposed", "retesting", "retest_passed", "retest_failed", "rejected", "invalid", "exported"];

export function ReviewsPage() {
  const [status, setStatus] = useState("proposed");
  const [open, setOpen] = useState<number | null>(null);
  const patches = useApi<Patch[]>(`/api/patches${status ? `?status=${status}` : ""}`, 4000);

  return (
    <div className="stack">
      <PageHeader title="Patch reviews" description="Every proposed change waits here for a human decision. Nothing is applied to a repository automatically." />
      <div className="row">
        <select className="select" value={status} onChange={(e) => { setStatus(e.target.value); setOpen(null); }} aria-label="Patch status filter">
          {FILTERS.map((f) => <option key={f} value={f}>{f ? f.replace(/_/g, " ") : "All statuses"}</option>)}
        </select>
      </div>
      <ErrorBanner error={patches.error} />
      <Card bodyClass="table-wrap">
        {!patches.data ? <Loading /> : !patches.data.length ? (
          <Empty title="No patches in this state" />
        ) : (
          <table className="table">
            <thead><tr><th>Patch</th><th>Run</th><th>Source</th><th>Status</th><th>Risk</th><th>Files</th><th>Created</th></tr></thead>
            <tbody>
              {patches.data.map((p) => (
                <tr key={p.id} className={`clickable${open === p.id ? " selected" : ""}`} onClick={() => setOpen(open === p.id ? null : p.id)}>
                  <td>#{p.id}</td>
                  <td><Link to={`/runs/${p.run_id}`} onClick={(e) => e.stopPropagation()}>#{p.run_id}</Link></td>
                  <td>{p.provider} {p.is_ai ? <span className="badge badge-info">AI</span> : <span className="badge">not AI</span>}</td>
                  <td><StatusBadge status={p.status} /></td>
                  <td><StatusBadge status={p.risk_level} label={`${p.risk_level} · ${p.risk_score}`} /></td>
                  <td className="mono small">{p.files.join(", ")}</td>
                  <td className="secondary">{formatTime(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {open !== null && <PatchReview key={open} patchId={open} />}
    </div>
  );
}
