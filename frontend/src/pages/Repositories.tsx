import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Banner, Card, Empty, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { api, post } from "../services/api";
import { relativeTime, shortSha } from "../services/format";
import type { Repository, RepositoryDetail, Run } from "../services/types";
import { useApi } from "../services/useApi";

function Languages({ languages }: { languages: Record<string, number> }) {
  const entries = Object.entries(languages);
  if (!entries.length) return <span className="muted">—</span>;
  return <span className="row">{entries.slice(0, 3).map(([lang, pct]) => <span key={lang} className="chip">{lang} {pct}%</span>)}</span>;
}

export function RepositoriesPage() {
  const repos = useApi<Repository[]>("/api/repositories");
  const [selected, setSelected] = useState<number | null>(null);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
      await repos.reload();
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  function register(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      const repo = await post<Repository>("/api/repositories", { path: path.trim(), name: name.trim() || null });
      setPath("");
      setName("");
      setSelected(repo.id);
    });
  }

  return (
    <div className="stack">
      <PageHeader
        title="Repositories"
        description="Registered Git repositories. DevFlow reads commits directly from Git and never runs repository hooks."
        actions={
          <button className="btn" disabled={busy} onClick={() => void act(() => post("/api/repositories/examples"))}>
            Install example repositories
          </button>
        }
      />
      <ErrorBanner error={error ?? repos.error} />
      <Card title="Register a repository" subtitle="The path must be inside an approved directory (Settings › Repository directories).">
        <form className="row" onSubmit={register}>
          <input className="input" style={{ flex: "3 1 320px" }} placeholder="/Users/you/code/project" value={path}
            onChange={(e) => setPath(e.target.value)} aria-label="Repository path" required />
          <input className="input" style={{ flex: "1 1 160px" }} placeholder="Display name (optional)" value={name}
            onChange={(e) => setName(e.target.value)} aria-label="Display name" />
          <button className="btn btn-primary" type="submit" disabled={busy || !path.trim()}>Register</button>
        </form>
      </Card>
      <Card title="Registered repositories" bodyClass="table-wrap">
        {repos.loading && !repos.data ? <Loading /> : !repos.data?.length ? (
          <Empty title="No repositories registered">Install the example repositories to try DevFlow end to end.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr><th>Name</th><th>Languages</th><th>Tests</th><th>Last run</th><th /></tr>
            </thead>
            <tbody>
              {repos.data.map((repo) => (
                <tr key={repo.id} className={`clickable${selected === repo.id ? " selected" : ""}`} onClick={() => setSelected(repo.id)}>
                  <td>
                    <div style={{ fontWeight: 550 }}>{repo.name} {repo.is_example && <span className="chip">example</span>}</div>
                    <div className="muted small mono">{repo.path}</div>
                  </td>
                  <td><Languages languages={repo.languages} /></td>
                  <td>
                    {repo.structure.supported ? `${repo.structure.test_file_count} pytest file(s)` : <span className="muted">{repo.structure.unsupported_reason}</span>}
                  </td>
                  <td className="secondary">{relativeTime(repo.last_run_at)}</td>
                  <td className="num"><button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); setSelected(repo.id); }}>Details</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {selected !== null && <RepositoryPanel key={selected} repoId={selected} onRemoved={() => { setSelected(null); void repos.reload(); }} />}
    </div>
  );
}

function RepositoryPanel({ repoId, onRemoved }: { repoId: number; onRemoved: () => void }) {
  const detail = useApi<RepositoryDetail>(`/api/repositories/${repoId}`);
  const [revision, setRevision] = useState("HEAD");
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const d = detail.data;

  async function run(rev: string) {
    setBusy(true);
    setError(undefined);
    try {
      const created = await post<Run>("/api/runs", { repository_id: repoId, revision: rev });
      navigate(`/runs/${created.id}`);
    } catch (err) {
      setError(err as Error);
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm("Unregister this repository and delete its DevFlow history? Files on disk are not touched.")) return;
    try {
      await api(`/api/repositories/${repoId}`, { method: "DELETE" });
      onRemoved();
    } catch (err) {
      setError(err as Error);
    }
  }

  if (detail.error) return <ErrorBanner error={detail.error} />;
  if (!d) return <Loading />;
  return (
    <Card
      title={d.name}
      subtitle={<span className="mono">{d.path}</span>}
      actions={<button className="btn btn-sm btn-danger" onClick={() => void remove()}>Unregister</button>}
    >
      <div className="stack">
        <ErrorBanner error={error} />
        {!d.structure.supported && <Banner tone="warning">This repository cannot be tested: {d.structure.unsupported_reason}</Banner>}
        <div className="grid grid-2">
          <dl className="kv">
            <dt>Branch</dt><dd className="mono">{d.branch ?? "(detached HEAD)"}</dd>
            <dt>HEAD</dt><dd className="mono">{shortSha(d.head)}</dd>
            <dt>Files</dt><dd>{d.structure.file_count}</dd>
            <dt>Markers</dt><dd>{d.structure.markers?.join(", ") || "—"}</dd>
          </dl>
          <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); void run(revision); }}>
            <label className="field">
              Revision to test
              <select className="select" value={revision} onChange={(e) => setRevision(e.target.value)}>
                <option value="HEAD">HEAD ({shortSha(d.head)})</option>
                {d.branches.map((b) => <option key={b} value={b}>branch: {b}</option>)}
                {d.recent_commits.map((c) => <option key={c.sha} value={c.sha}>{shortSha(c.sha)} {c.message.slice(0, 50)}</option>)}
              </select>
            </label>
            <div>
              <button className="btn btn-primary" type="submit" disabled={busy || !d.structure.supported}>
                <Icon name="play" size={14} /> {busy ? "Starting…" : "Run tests"}
              </button>
            </div>
          </form>
        </div>
        <div>
          <h3 style={{ marginBottom: 8 }}>Recent commits</h3>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Commit</th><th>Message</th><th>Author</th><th>Date</th><th /></tr></thead>
              <tbody>
                {d.recent_commits.map((c) => (
                  <tr key={c.sha}>
                    <td className="mono">{shortSha(c.sha)}</td>
                    <td>{c.message}</td>
                    <td className="secondary">{c.author}</td>
                    <td className="secondary">{relativeTime(c.committed_at)}</td>
                    <td className="num"><button className="btn btn-sm" disabled={busy || !d.structure.supported} onClick={() => void run(c.sha)}>Run</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div><Link to={`/builds?repository=${repoId}`}>View this repository's build history →</Link></div>
      </div>
    </Card>
  );
}
