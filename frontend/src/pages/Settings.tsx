import { type FormEvent, useEffect, useState } from "react";
import { Banner, Card, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { api } from "../services/api";
import type { Provider, RuntimeConfig, SettingsResponse } from "../services/types";
import { useApi } from "../services/useApi";

export function SettingsPage() {
  const settings = useApi<SettingsResponse>("/api/settings");
  const providers = useApi<Provider[]>("/api/providers");
  const [form, setForm] = useState<RuntimeConfig>();
  const [roots, setRoots] = useState("");
  const [error, setError] = useState<Error>();
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (settings.data && !form) {
      setForm(settings.data.runtime);
      setRoots(settings.data.runtime.repository_roots.join("\n"));
    }
  }, [settings.data, form]);

  if (settings.error) return <ErrorBanner error={settings.error} />;
  if (!form || !settings.data) return <Loading />;
  const s = settings.data.static;

  function set<K extends keyof RuntimeConfig>(key: K, value: RuntimeConfig[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    setSaved(false);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(undefined);
    try {
      const body = { ...form, repository_roots: roots.split("\n").map((r) => r.trim()).filter(Boolean) };
      const result = await api<SettingsResponse>("/api/settings", { method: "PUT", json: body });
      setForm(result.runtime);
      setRoots(result.runtime.repository_roots.join("\n"));
      setSaved(true);
      await providers.reload();
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={save}>
      <PageHeader title="Settings" description="Runtime configuration. Limits are bounded server-side; container networking cannot be enabled."
        actions={<button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save changes"}</button>} />
      <ErrorBanner error={error} />
      {saved && <Banner tone="info">Settings saved.</Banner>}

      <Card title="Repository directories" subtitle="Repositories can only be registered inside these directories. Home, root, and credential directories are refused.">
        <label className="field">
          One absolute path per line
          <textarea rows={3} value={roots} onChange={(e) => { setRoots(e.target.value); setSaved(false); }} placeholder="/Users/you/code" />
        </label>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Always allowed: <code>{String(s.managed_repos_dir)}</code> (bundled examples)
          {Array.isArray(s.env_allowed_roots) && s.env_allowed_roots.length > 0 && <> and from the environment: <code>{s.env_allowed_roots.join(", ")}</code></>}
        </p>
      </Card>

      <div className="grid grid-2">
        <Card title="Test execution" subtitle={`Runner: ${s.runner} · image ${s.runner_image} · network ${s.network_access}`}>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
            <label className="field">Timeout (seconds) <span className="field-hint">5–1800</span>
              <input className="input" type="number" min={5} max={1800} value={form.test_timeout_seconds} onChange={(e) => set("test_timeout_seconds", Number(e.target.value))} />
            </label>
            <label className="field">Memory limit (MB) <span className="field-hint">128–4096</span>
              <input className="input" type="number" min={128} max={4096} step={64} value={form.memory_mb} onChange={(e) => set("memory_mb", Number(e.target.value))} />
            </label>
            <label className="field">CPU limit <span className="field-hint">0.25–4 cores</span>
              <input className="input" type="number" min={0.25} max={4} step={0.25} value={form.cpus} onChange={(e) => set("cpus", Number(e.target.value))} />
            </label>
            <label className="field">Process limit <span className="field-hint">32–2048</span>
              <input className="input" type="number" min={32} max={2048} value={form.pids_limit} onChange={(e) => set("pids_limit", Number(e.target.value))} />
            </label>
          </div>
        </Card>

        <Card title="AI integration" subtitle="AI is optional. Testing, history, and metrics work without it.">
          <div className="stack" style={{ gap: 12 }}>
            <label className="row">
              <input type="checkbox" checked={form.ai_enabled} onChange={(e) => set("ai_enabled", e.target.checked)} />
              Enable AI providers (Claude Code, Codex)
            </label>
            <label className="field">Default provider
              <select className="select" value={form.ai_provider} onChange={(e) => set("ai_provider", e.target.value as RuntimeConfig["ai_provider"])}>
                {providers.data?.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
              </select>
            </label>
            <label className="field">How AI providers fix code
              <select className="select" value={form.ai_mode} onChange={(e) => set("ai_mode", e.target.value as RuntimeConfig["ai_mode"])}>
                <option value="agent">Agent mode: edit a sandboxed copy, DevFlow computes the diff</option>
                <option value="text">Text mode: provider writes a diff</option>
              </select>
            </label>
            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
              <label className="field">Max attempts <span className="field-hint">1–5, failures are fed back</span>
                <input className="input" type="number" min={1} max={5} value={form.ai_max_attempts} onChange={(e) => set("ai_max_attempts", Number(e.target.value))} />
              </label>
              <label className="row" style={{ alignSelf: "end", height: 32 }}>
                <input type="checkbox" checked={form.ai_verify_patches} onChange={(e) => set("ai_verify_patches", e.target.checked)} />
                Test candidates in the sandbox
              </label>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Provider</th><th>Status</th></tr></thead>
                <tbody>
                  {providers.data?.map((p) => (
                    <tr key={p.name}>
                      <td>{p.label}<div className="small muted">{p.version}</div></td>
                      <td className="small">{p.available ? "available" : "unavailable"}{p.reason && <div className="muted">{p.reason}</div>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      </div>

      <Card title="Logging">
        <label className="field" style={{ maxWidth: 240 }}>Log level
          <select className="select" value={form.log_level} onChange={(e) => set("log_level", e.target.value as RuntimeConfig["log_level"])}>
            {["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Structured JSON logs go to stderr; traces to <code>{String(s.data_dir)}/traces.jsonl</code>; Prometheus metrics at <code>/metrics</code>. Server bound to <code>{String(s.bind)}</code>.
        </p>
      </Card>
    </form>
  );
}
