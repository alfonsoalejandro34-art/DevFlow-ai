export type RunStatus = "queued" | "running" | "passed" | "failed" | "error" | "timeout" | "cancelled";

export interface Repository {
  id: number;
  name: string;
  path: string;
  is_example: boolean;
  languages: Record<string, number>;
  structure: {
    file_count?: number;
    test_framework?: string | null;
    test_file_count?: number;
    supported?: boolean;
    unsupported_reason?: string | null;
    markers?: string[];
  };
  created_at: string;
  last_run_at: string | null;
}

export interface Commit {
  sha: string;
  author: string;
  committed_at: string;
  message: string;
}

export interface RepositoryDetail extends Repository {
  branch: string | null;
  head: string | null;
  branches: string[];
  recent_commits: Commit[];
}

export interface Run {
  id: number;
  repository_id: number;
  repository_name: string | null;
  kind: "revision" | "patch_retest";
  parent_run_id: number | null;
  patch_id: number | null;
  status: RunStatus;
  runner: string;
  sandboxed: boolean;
  trigger: string;
  correlation_id: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  test_duration_ms: number | null;
  overhead_ms: number | null;
  exit_code: number | null;
  total: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  error_message: string | null;
  source_info: { sha?: string; ref?: string; patch_id?: number };
  revision: { sha: string; ref: string | null; message: string; author: string } | null;
}

export interface RunPage {
  items: Run[];
  total: number;
  limit: number;
  offset: number;
}

export interface TestResult {
  id: number;
  nodeid: string;
  file: string | null;
  name: string;
  outcome: "passed" | "failed" | "error" | "skipped";
  duration_ms: number;
  message: string | null;
  traceback: string | null;
}

export interface Failure {
  id: number;
  run_id: number;
  category: string;
  exception_type: string | null;
  signature: string;
  nodeid: string | null;
  summary: string;
  file: string | null;
  line: number | null;
  created_at: string;
  resolved_by_run_id: number | null;
  resolved_at: string | null;
  occurrences: number;
  traceback: string | null;
}

export interface Analysis {
  id: number;
  run_id: number;
  provider: string;
  is_ai: boolean;
  status: "pending" | "running" | "completed" | "failed" | "unavailable";
  created_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  root_cause: string | null;
  explanation: string | null;
  confidence: string | null;
  error: string | null;
  warnings: string[];
  context_files: string[];
  patch_ids: number[];
}

export interface Decision {
  id: number;
  decision: string;
  actor: string;
  comment: string | null;
  created_at: string;
}

export interface Patch {
  id: number;
  analysis_id: number;
  run_id: number;
  status: string;
  diff: string;
  files: string[];
  rationale: string | null;
  risk_level: "low" | "medium" | "high";
  risk_score: number;
  risk_reasons: string[];
  validation_errors: string[];
  lines_added: number;
  lines_removed: number;
  retest_run_id: number | null;
  exported_branch: string | null;
  exported_commit: string | null;
  created_at: string;
  decided_at: string | null;
  decisions: Decision[];
  provider: string | null;
  is_ai: boolean | null;
}

export interface Provider {
  name: string;
  label: string;
  is_ai: boolean;
  generates_patches: boolean;
  available: boolean;
  reason: string | null;
  version: string | null;
  default: boolean;
}

export interface Comparison {
  run_id: number;
  baseline_run_id: number;
  fixed: string[];
  regressed: string[];
  still_failing: string[];
  added: string[];
  removed: string[];
}

export interface Health {
  status: "ok" | "degraded";
  version: string;
  database: boolean;
  runner: { name: string; sandboxed: boolean; available: boolean; reason: string | null; version: string | null };
  active_jobs: number;
  warnings: { level: "warning" | "error"; message: string }[];
}

export interface Overview {
  runs: {
    total: number;
    active: number;
    completed: number;
    passed: number;
    failed_builds: number;
    cancelled: number;
    pass_rate: number | null;
    avg_duration_ms: number | null;
    median_duration_ms: number | null;
    sandboxed_share: number | null;
  };
  productivity: {
    avg_time_to_failure_ms: number | null;
    avg_time_to_validate_fix_ms: number | null;
    avg_diagnosis_ms: number | null;
    avg_runner_overhead_ms: number | null;
    repeated_failures: number;
    resolved_failures: number;
    open_failures: number;
    failure_categories: Record<string, number>;
  };
  ai: {
    analyses: number;
    ai_analyses: number;
    by_provider: Record<string, number>;
    by_status: Record<string, number>;
    proposals: number;
    invalid_proposals: number;
    approved: number;
    rejected: number;
    acceptance_rate: number | null;
    ai_acceptance_rate: number | null;
    retest_passed: number;
    retest_failed: number;
  };
  recent_runs: {
    id: number;
    repository: string | null;
    status: RunStatus;
    kind: string;
    created_at: string;
    passed: number;
    failed: number;
    duration_ms: number | null;
  }[];
}

export interface DailyPoint {
  date: string;
  runs: number;
  passed: number;
  failed: number;
  pass_rate: number | null;
  avg_duration_ms: number | null;
  failures_by_category: Record<string, number>;
  acceptance_rate: number | null;
}

export interface Timeseries {
  days: number;
  daily: DailyPoint[];
  runs: {
    id: number;
    status: RunStatus;
    duration_ms: number | null;
    test_duration_ms: number | null;
    overhead_ms: number | null;
    passed: number;
    failed: number;
  }[];
}

export interface BenchmarkRow {
  batch_id: string;
  scenario: string;
  mode: string;
  iterations: number;
  successes: number;
  median_ms: number | null;
  p90_ms: number | null;
  min_ms: number | null;
  max_ms: number | null;
  stdev_ms: number | null;
  created_at: string;
}

export interface RuntimeConfig {
  test_timeout_seconds: number;
  memory_mb: number;
  cpus: number;
  pids_limit: number;
  ai_enabled: boolean;
  ai_provider: "heuristic" | "claude-code" | "codex" | "demo";
  repository_roots: string[];
  log_level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
}

export interface SettingsResponse {
  runtime: RuntimeConfig;
  static: Record<string, string | number | boolean | string[]>;
}
