import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { setToken } from "../services/api";
import { BuildsPage } from "../pages/Builds";
import { PatchReview } from "../pages/PatchReview";
import { SignInPage } from "../pages/SignIn";
import type { Patch } from "../services/types";
import { mockFetch, renderWithRouter } from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
  setToken(null);
});

const basePatch: Patch = {
  id: 3, analysis_id: 1, run_id: 9, status: "proposed",
  diff: "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n", files: ["x.py"], rationale: "fixes it",
  risk_level: "low", risk_score: 0, risk_reasons: ["small, localized change to non-test source"],
  validation_errors: [], lines_added: 1, lines_removed: 1, retest_run_id: null, exported_branch: null,
  exported_commit: null, created_at: "2026-11-12T10:00:00Z", decided_at: null, decisions: [], provider: "codex", is_ai: true,
};

describe("SignInPage", () => {
  it("shows the server error for a bad token and does not sign in", async () => {
    mockFetch(() => new Response(JSON.stringify({ error: { code: "unauthorized", message: "Missing or invalid API token" } }), { status: 401 }));
    const onSignedIn = vi.fn();
    render(<SignInPage onSignedIn={onSignedIn} />);
    await userEvent.type(screen.getByLabelText("API token"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Missing or invalid API token");
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it("sends the token as a bearer header and signs in", async () => {
    const calls = mockFetch(() => []);
    const onSignedIn = vi.fn();
    render(<SignInPage onSignedIn={onSignedIn} />);
    await userEvent.type(screen.getByLabelText("API token"), "secret-token");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer secret-token");
  });
});

describe("PatchReview", () => {
  it("requires an explicit approve click and posts the decision", async () => {
    let current = basePatch;
    const calls = mockFetch((url, init) => {
      if (url.endsWith("/review") && init?.method === "POST") {
        current = { ...basePatch, status: "retesting", retest_run_id: 11 };
        return current;
      }
      return current;
    });
    renderWithRouter(<PatchReview patchId={3} />);
    expect(await screen.findByText("Patch #3")).toBeInTheDocument();
    expect(calls.filter((c) => c.init?.method === "POST")).toHaveLength(0); // nothing happens on view
    await userEvent.type(screen.getByLabelText(/Review comment/), "looks right");
    await userEvent.click(screen.getByRole("button", { name: "Approve and retest" }));
    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe("/api/patches/3/review");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ decision: "approve", comment: "looks right" });
    expect(await screen.findByRole("link", { name: "run #11" })).toHaveAttribute("href", "/runs/11");
  });

  it("hides review actions for invalid patches and shows why", async () => {
    mockFetch(() => ({ ...basePatch, status: "invalid", validation_errors: ["Unsafe path: '../x'"] }));
    renderWithRouter(<PatchReview patchId={3} />);
    expect(await screen.findByText(/Rejected by validation/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and retest" })).not.toBeInTheDocument();
  });

  it("keeps export disabled until the user acknowledges writing a branch", async () => {
    const calls = mockFetch(() => ({ ...basePatch, status: "retest_passed", retest_run_id: 11 }));
    renderWithRouter(<PatchReview patchId={3} />);
    const exportButton = await screen.findByRole("button", { name: "Export branch" });
    expect(exportButton).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/I understand/));
    expect(exportButton).toBeEnabled();
    await userEvent.click(exportButton);
    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe("/api/patches/3/export");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ confirm: true, branch: null });
  });
});

describe("BuildsPage", () => {
  it("lists runs and applies filters to the API query", async () => {
    const calls = mockFetch((url) => {
      if (url.startsWith("/api/repositories")) return [{ id: 1, name: "inventory-service" }];
      return {
        total: 1, limit: 25, offset: 0,
        items: [{ id: 4, repository_id: 1, repository_name: "inventory-service", kind: "revision", patch_id: null,
          status: "failed", runner: "docker", sandboxed: true, created_at: "2026-11-12T10:00:00Z", duration_ms: 2300,
          passed: 9, failed: 5, errors: 0, source_info: { sha: "abcdef1234567890" }, revision: null }],
      };
    });
    renderWithRouter(<BuildsPage />, "/builds");
    expect(await screen.findByText("#4")).toBeInTheDocument();
    expect(screen.getByText("abcdef12")).toBeInTheDocument();
    expect(screen.getByText("failed", { selector: ".badge" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Status filter"), "failed");
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=failed"))).toBe(true));
  });
});
