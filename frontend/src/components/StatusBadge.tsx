const TONES: Record<string, string> = {
  passed: "badge-good",
  retest_passed: "badge-good",
  exported: "badge-good",
  completed: "badge-good",
  approved: "badge-info",
  failed: "badge-bad",
  error: "badge-bad",
  timeout: "badge-bad",
  retest_failed: "badge-bad",
  invalid: "badge-bad",
  rejected: "badge-warn",
  unavailable: "badge-warn",
  cancelled: "",
  queued: "badge-info badge-running",
  running: "badge-info badge-running",
  pending: "badge-info badge-running",
  retesting: "badge-info badge-running",
  proposed: "badge-warn",
  low: "badge-good",
  medium: "badge-warn",
  high: "badge-bad",
};

/** Status is always shown as dot + text so it never relies on color alone. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <span className={`badge ${TONES[status] ?? ""}`} data-status={status}>
      <span className="badge-dot" aria-hidden="true" />
      {label ?? status.replace(/_/g, " ")}
    </span>
  );
}
