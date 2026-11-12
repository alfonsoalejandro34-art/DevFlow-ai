import { Suspense } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { setToken } from "../services/api";
import type { Health } from "../services/types";
import { useApi } from "../services/useApi";
import { Icon } from "./Icon";
import { StatusBadge } from "./StatusBadge";
import { Banner } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: "overview" },
  { to: "/repositories", label: "Repositories", icon: "repo" },
  { to: "/builds", label: "Build history", icon: "builds" },
  { to: "/reviews", label: "Patch reviews", icon: "review" },
  { to: "/analytics", label: "Analytics", icon: "analytics" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

export function Layout({ onSignOut }: { onSignOut: () => void }) {
  const health = useApi<Health>("/api/health", 10_000);
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 12h5l2-6 4 12 2-6h5" />
            </svg>
          </span>
          DevFlow AI
        </div>
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <Icon name={item.icon} />
            {item.label}
          </NavLink>
        ))}
        <div className="sidebar-footer">
          {health.data && (
            <>
              <div className="spread">
                <span>Service</span>
                <StatusBadge status={health.data.status === "ok" ? "passed" : "error"} label={health.data.status} />
              </div>
              <div className="spread">
                <span>Runner</span>
                <span className="mono">{health.data.runner.name}{health.data.runner.sandboxed ? "" : " (unsandboxed)"}</span>
              </div>
              <div className="spread">
                <span>Version</span>
                <span className="mono">{health.data.version}</span>
              </div>
            </>
          )}
          {health.error && <span>Backend unreachable</span>}
          <button className="btn btn-sm" onClick={() => { setToken(null); onSignOut(); }}>
            <Icon name="logout" size={14} /> Sign out
          </button>
        </div>
      </nav>
      <main className="main">
        <div className="stack" style={{ gap: 8, marginBottom: health.data?.warnings.length || health.error ? 16 : 0 }}>
          {health.error && <Banner tone="error">Cannot reach the DevFlow backend: {health.error.message}</Banner>}
          {health.data?.warnings.map((w) => (
            <Banner key={w.message} tone={w.level === "error" ? "error" : "warning"}>{w.message}</Banner>
          ))}
        </div>
        <Suspense fallback={<div className="empty">Loading…</div>}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
