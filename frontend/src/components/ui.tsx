import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function Card({ title, subtitle, actions, children, bodyClass = "card-body" }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  bodyClass?: string;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="card stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Banner({ tone, children }: { tone: "error" | "warning" | "info"; children: ReactNode }) {
  return (
    <div className={`banner banner-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon name={tone === "info" ? "info" : "alert"} />
      <div>{children}</div>
    </div>
  );
}

export function ErrorBanner({ error }: { error: Error | undefined | null }) {
  if (!error) return null;
  return <Banner tone="error">{error.message}</Banner>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export function Loading() {
  return <div className="empty">Loading…</div>;
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}
