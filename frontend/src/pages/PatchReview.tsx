import { useState } from "react";
import { Link } from "react-router-dom";
import { DiffView } from "../components/CodeViews";
import { StatusBadge } from "../components/StatusBadge";
import { Banner, ErrorBanner, Loading } from "../components/ui";
import { post } from "../services/api";
import { formatTime, shortSha } from "../services/format";
import type { Patch, Run } from "../services/types";
import { useApi } from "../services/useApi";

const LIVE = new Set(["retesting"]);

export function PatchReview({ patchId }: { patchId: number }) {
  const [live, setLive] = useState(true);
  const patch = useApi<Patch>(`/api/patches/${patchId}`, live && 1500);
  const [comment, setComment] = useState("");
  const [branch, setBranch] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const p = patch.data;
  if (p && LIVE.has(p.status) !== live) setLive(LIVE.has(p.status));

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError(undefined);
    try {
      await post(path, body);
      await patch.reload();
      setLive(true);
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  if (patch.error) return <ErrorBanner error={patch.error} />;
  if (!p) return <Loading />;

  return (
    <div className="card" style={{ background: "var(--surface-2)" }}>
      <div className="card-body stack" style={{ gap: 12 }}>
        <div className="spread">
          <div className="row">
            <h3>Patch #{p.id}</h3>
            <StatusBadge status={p.status} />
            <StatusBadge status={p.risk_level} label={`${p.risk_level} risk · ${p.risk_score}`} />
            <span className="small muted">+{p.lines_added} −{p.lines_removed} in {p.files.length} file(s)</span>
          </div>
          <div className="row">
            {p.attempt > 1 && <span className="chip">attempt {p.attempt}</span>}
            {p.is_ai === false && <span className="chip">scripted / non-AI</span>}
          </div>
        </div>
        <ErrorBanner error={error} />
        {p.validation_errors.length > 0 && (
          <Banner tone="error"><strong>Rejected by validation:</strong> {p.validation_errors.join("; ")}</Banner>
        )}
        {p.verification_run_id && (
          <div className="row small">
            Pre-review check: <Link to={`/runs/${p.verification_run_id}`}>run #{p.verification_run_id}</Link>
            <VerificationBadge runId={p.verification_run_id} />
          </div>
        )}
        {p.rationale && <p style={{ margin: 0 }} className="secondary"><strong>Why this should work:</strong> {p.rationale}</p>}
        <ul className="small secondary" style={{ margin: 0, paddingLeft: 18 }}>
          {p.risk_reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
        <DiffView diff={p.diff} />

        {p.status === "proposed" && (
          <div className="stack" style={{ gap: 8 }}>
            <label className="field">
              Review comment <span className="field-hint">(optional, stored with the decision)</span>
              <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
            <div className="row">
              <button className="btn btn-good" disabled={busy}
                onClick={() => void act(`/api/patches/${p.id}/review`, { decision: "approve", comment: comment || null })}>
                Approve and retest
              </button>
              <button className="btn btn-danger" disabled={busy}
                onClick={() => void act(`/api/patches/${p.id}/review`, { decision: "reject", comment: comment || null })}>
                Reject
              </button>
              <span className="small muted">Approval retests the patched code in a sandbox. Your repository is not modified.</span>
            </div>
          </div>
        )}

        {p.retest_run_id && (
          <div className="row">
            Retest: <Link to={`/runs/${p.retest_run_id}`}>run #{p.retest_run_id}</Link>
            {p.status === "retesting" && <StatusBadge status="running" label="in progress" />}
            {p.status === "retest_passed" && <StatusBadge status="passed" label="all tests pass" />}
            {p.status === "retest_failed" && <StatusBadge status="failed" label="tests still failing" />}
          </div>
        )}
        {(p.status === "approved" || p.status === "retest_failed") && (
          <div><button className="btn btn-sm" disabled={busy} onClick={() => void act(`/api/patches/${p.id}/retest`)}>Run retest again</button></div>
        )}

        {p.status === "retest_passed" && (
          <div className="stack" style={{ gap: 8, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            <h3>Export to a branch</h3>
            <p className="small secondary" style={{ margin: 0 }}>
              Creates a new branch with this patch committed on top of the tested commit. Your working tree, index, and current branch are not changed. Nothing is pushed or merged.
            </p>
            <div className="row">
              <input className="input" placeholder={`devflow/patch-${p.id}`} value={branch} onChange={(e) => setBranch(e.target.value)} aria-label="Branch name" />
              <label className="row small">
                <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
                I understand this writes a branch to my repository
              </label>
              <button className="btn btn-primary" disabled={busy || !acknowledged}
                onClick={() => void act(`/api/patches/${p.id}/export`, { confirm: true, branch: branch.trim() || null })}>
                Export branch
              </button>
            </div>
          </div>
        )}
        {p.exported_branch && (
          <Banner tone="info">Exported to branch <code>{p.exported_branch}</code> at <code>{shortSha(p.exported_commit)}</code>.</Banner>
        )}

        {p.decisions.length > 0 && (
          <div className="small muted">
            {p.decisions.map((d) => (
              <div key={d.id}>{formatTime(d.created_at)} · {d.actor} · <strong>{d.decision}</strong>{d.comment && <> — “{d.comment}”</>}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function VerificationBadge({ runId }: { runId: number }) {
  const run = useApi<Run>(`/api/runs/${runId}`);
  if (!run.data) return null;
  return run.data.status === "passed" ? (
    <StatusBadge status="passed" label="all tests pass in sandbox" />
  ) : (
    <StatusBadge status="failed" label={`${run.data.failed + run.data.errors} test(s) still failing`} />
  );
}
