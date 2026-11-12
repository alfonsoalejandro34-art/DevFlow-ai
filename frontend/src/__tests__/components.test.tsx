import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DiffView } from "../components/CodeViews";
import { StatusBadge } from "../components/StatusBadge";
import { formatMs, formatPercent, relativeTime, shortSha } from "../services/format";

describe("StatusBadge", () => {
  it("always renders a text label, not color alone", () => {
    render(<StatusBadge status="retest_failed" />);
    const badge = screen.getByText("retest failed");
    expect(badge).toHaveClass("badge-bad");
    expect(badge).toHaveAttribute("data-status", "retest_failed");
  });
});

describe("DiffView", () => {
  it("classifies added, removed, hunk and metadata lines", () => {
    const { container } = render(<DiffView diff={"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n ctx\n"} />);
    const lines = [...container.querySelectorAll(".diff-line")];
    expect(lines.map((l) => l.className.replace("diff-line", "").trim())).toEqual(["diff-meta", "diff-meta", "diff-hunk", "diff-del", "diff-add", ""]);
  });
});

describe("format", () => {
  it("formats durations, percents and SHAs", () => {
    expect(formatMs(null)).toBe("—");
    expect(formatMs(450)).toBe("450 ms");
    expect(formatMs(1500)).toBe("1.5 s");
    expect(formatMs(125_000)).toBe("2m 5s");
    expect(formatPercent(50)).toBe("50%");
    expect(formatPercent(33.333)).toBe("33.3%");
    expect(shortSha("0123456789abcdef")).toBe("01234567");
    expect(relativeTime(new Date(Date.now() - 7200_000).toISOString())).toBe("2h ago");
  });
});
