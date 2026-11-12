import { type FormEvent, useState } from "react";
import { ErrorBanner } from "../components/ui";
import { api, setToken } from "../services/api";

export function SignInPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setToken(value.trim());
    try {
      await api("/api/repositories");
      onSignedIn();
    } catch (err) {
      setToken(null);
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <form className="card" onSubmit={submit}>
        <div className="card-body stack">
          <div>
            <h1>DevFlow AI</h1>
            <p className="secondary" style={{ margin: "6px 0 0" }}>
              Paste the local API token. Print it with <code>devflow token</code>.
            </p>
          </div>
          <ErrorBanner error={error} />
          <label className="field">
            API token
            <input className="input" type="password" autoComplete="off" value={value}
              onChange={(e) => setValue(e.target.value)} required />
          </label>
          <button className="btn btn-primary" type="submit" disabled={busy || !value.trim()}>
            {busy ? "Checking…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}
