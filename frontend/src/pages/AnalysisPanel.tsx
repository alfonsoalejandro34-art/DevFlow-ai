import { useState } from "react";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { Banner, Card, Empty, ErrorBanner, Loading } from "../components/ui";
import { post } from "../services/api";
import { formatMs, formatTime } from "../services/format";
import type { Analysis, Provider, Run } from "../services/types";
import { useApi } from "../services/useApi";
import { PatchReview } from "./PatchReview";

const ANALYZABLE = new Set(["failed", "error", "timeout"]);

export function AnalysisPanel({ run }: { run: Run }) {
  const providers = useApi<Provider[]>("/api/providers");
  const [polling, setPolling] = useState(false);
  const analyses = useApi<Analysis[]>(`/api/analyses?run_id=${run.id}`, polling && 1500);
  const [provider, setProvider] = useState<string>("");
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);

  const pending = analyses.data?.some((a) => a.status === "pending" || a.status === "running") ?? false;
  if (pending !== polling) setPolling(pending);
  const chosen = provider || providers.data?.find((p) => p.default)?.name || "heuristic";
  const chosenProvider = providers.data?.find((p) => p.name === chosen);

  async function analyze() {
    setBusy(true);
    setError(undefined);
    try {
      await post("/api/analyses", { run_id: run.id, provider: chosen });
      await analyses.reload();
      setPolling(true);
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <Card title="Diagnose this run" subtitle="Providers receive only the failing tests and the source files they reference, with secrets redacted.">
        {!ANALYZABLE.has(run.status) ? (
          <Empty title="Nothing to diagnose">Only failed, errored, or timed-out runs can be analyzed.</Empty>
        ) : (
          <div className="stack" style={{ gap: 10 }}>
            <ErrorBanner error={error ?? providers.error} />
            <div className="row">
              <select className="select" value={chosen} onChange={(e) => setProvider(e.target.value)} aria-label="Analysis provider">
                {providers.data?.map((p) => (
                  <option key={p.name} value={p.name} disabled={!p.available}>
                    {p.label}{p.available ? "" : " (unavailable)"}
                  </option>
                ))}
              </select>
              <button className="btn btn-primary" onClick={() => void analyze()} disabled={busy || pending || !chosenProvider?.available}>
                <Icon name="sparkle" size={14} /> {pending ? "Analyzing…" : "Analyze failures"}
              </button>
            </div>
            {chosenProvider && (
              <div className="small muted">
                {chosenProvider.is_ai ? "AI provider" : "Not AI"} · {chosenProvider.generates_patches ? "proposes patches" : "diagnosis only"}
                {chosenProvider.version && <> · {chosenProvider.version}</>}
                {chosenProvider.reason && <> · {chosenProvider.reason}</>}
              </div>
            )}
          </div>
        )}
      </Card>
      {analyses.data === undefined ? <Loading /> : analyses.data.map((a) => <AnalysisCard key={a.id} analysis={a} />)}
    </div>
  );
}

function AnalysisCard({ analysis: a }: { analysis: Analysis }) {
  return (
    <Card
      title={<span className="row">Analysis #{a.id} <StatusBadge status={a.status} />
        <span className={`badge ${a.is_ai ? "badge-info" : ""}`}>{a.is_ai ? "AI" : "not AI"} · {a.provider}</span></span>}
      subtitle={`${formatTime(a.created_at)} · ${formatMs(a.duration_ms)}${a.confidence ? ` · confidence ${a.confidence}` : ""}`}
    >
      <div className="stack" style={{ gap: 12 }}>
        {a.error && <Banner tone={a.status === "unavailable" ? "warning" : "error"}>{a.error}</Banner>}
        {a.warnings.length > 0 && (
          <Banner tone="warning">
            <strong>Possible prompt injection in repository content:</strong> {a.warnings.join("; ")}. Review any proposed patch with extra care.
          </Banner>
        )}
        {a.root_cause && (
          <div>
            <h3>Root cause</h3>
            <p style={{ margin: "4px 0 0" }}>{a.root_cause}</p>
          </div>
        )}
        {a.explanation && (
          <div>
            <h3>Explanation</h3>
            <div className="secondary" style={{ whiteSpace: "pre-wrap", marginTop: 4, fontFamily: a.provider === "heuristic" ? "var(--mono)" : undefined, fontSize: a.provider === "heuristic" ? 12 : undefined }}>{a.explanation}</div>
          </div>
        )}
        {a.context_files.length > 0 && (
          <div className="small muted">Context sent: {a.context_files.map((f) => <span key={f} className="chip" style={{ marginRight: 4 }}>{f}</span>)}</div>
        )}
        {a.patch_ids.map((id) => <PatchReview key={id} patchId={id} />)}
      </div>
    </Card>
  );
}
