/**
 * TOOL_CARDS plan step 2 (2026-09-16) — Chat + detail binding tests.
 *
 * Pins (plan §3 step 2 + done-gate):
 *  - row-per-tool render matrix: every tool badge shows its audit-ready
 *    one-liner derived from `detail` (read window, write/create stats,
 *    bash command, grep pattern+count, glob count, git verb+args, todo
 *    titles), falling back to the legacy `summary` when `detail` is absent.
 *  - expander open/close: bash (and others with detail) mount a collapse
 *    button; the collapsed row stays one line; clicking reveals/hides
 *    the enriched panel (bash 2K excerpt keeps `bash-output-<id>`).
 *  - same-window badge: a read whose (path, window) repeats a prior read
 *    in the turn gets the `same-window-<id>` affordance.
 *  - legacy-row degrade: rows with NO `detail` key (pre step-1 servers)
 *    render today's one-liner, with NO expander and NO same-window badge.
 * Also pins the shared `ToolDetailMeta` renderer the detail surface uses
 * (one builder -> both renderers).
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SegmentedBody, TurnTools } from "../Thread";
import { ToolDetailMeta } from "../ToolDetailMeta";
import type { ChatToolRow, ToolRowDetail } from "@/types";

/** Build a minimal chat row with optional detail. */
function row(partial: Partial<ChatToolRow> & { callID: string; tool: string }): ChatToolRow {
  return {
    status: "completed",
    summary: "",
    title: null,
    input: null,
    round: 0,
    detail: null,
    output_excerpt: null,
    ...partial,
  };
}

describe("row-per-tool one-liner matrix (from detail)", () => {
  it("read shows the structured window (F3)", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "r1",
            tool: "read",
            summary: "src/foo.ts",
            detail: { window: { shownFrom: 11, shownTo: 30, total: 500 } },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-r1");
    expect(el.textContent).toContain("Read");
    expect(el.textContent).toContain("src/foo.ts");
    expect(el.textContent).toContain("L11–30/500");
  });

  it("write shows create mode + line stats (F4)", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "w1",
            tool: "write",
            summary: "n.py",
            detail: { mode: "create", linesAdded: 2 },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-w1");
    expect(el.textContent).toContain("created");
    expect(el.textContent).toContain("+2");
  });

  it("bash shows the command (F2) in the one-liner", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "b1",
            tool: "bash",
            summary: "ls -la",
            detail: { command: "ls -la", exit: 0 },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-b1");
    expect(el.textContent).toContain("Bash");
    expect(el.textContent).toContain("ls -la");
  });

  it("grep shows pattern + path + matchCount (F6, never content)", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "g1",
            tool: "grep",
            detail: { pattern: "secret", path: "src", include: "*.py", matchCount: 42 },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-g1");
    expect(el.textContent).toContain("“secret”");
    expect(el.textContent).toContain("in src");
    expect(el.textContent).toContain("(42)");
    // Never dumps match content in the one-liner.
    expect(el.textContent).not.toContain("content");
  });

  it("glob shows pattern + count", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "gl1",
            tool: "glob",
            detail: { pattern: "**/*.py", count: 12 },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-gl1");
    expect(el.textContent).toContain("**/*.py");
    expect(el.textContent).toContain("(12)");
  });

  it("git shows verb + args", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "gi1",
            tool: "git",
            detail: { verb: "log", args: ["-n", "3"] },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-gi1");
    expect(el.textContent).toContain("log");
    expect(el.textContent).toContain("-n 3");
  });

  it("todo shows the first title + overflow count", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "t1",
            tool: "todo",
            detail: { titles: ["step A", "step B", "step C"] },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-t1");
    expect(el.textContent).toContain("step A");
    expect(el.textContent).toContain("+2");
  });
});

describe("expander open/close", () => {
  it("bash row is collapsed by default and reveals the 2K excerpt", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "b2",
            tool: "bash",
            summary: "echo hi",
            detail: { command: "echo hi", exit: 0, output_excerpt: "hi\nmore", truncated: false },
          }),
        ]}
      />,
    );
    // Collapsed: the output panel is NOT mounted yet.
    expect(screen.queryByTestId("bash-output-b2")).toBeNull();
    expect(screen.queryByTestId("tool-detail-b2")).toBeNull();
    // The expander button exists (collapsed row stays one line tall).
    const btn = screen.getByTestId("tool-expand-b2");
    expect(btn.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(btn);
    expect(screen.getByTestId("tool-detail-b2")).toBeDefined();
    expect(screen.getByTestId("bash-output-b2").textContent).toContain("hi");
    expect(btn.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(btn);
    expect(screen.queryByTestId("tool-detail-b2")).toBeNull();
    expect(btn.getAttribute("aria-expanded")).toBe("false");
  });

  it("read shows the window inline (no expander needed)", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "r2",
            tool: "read",
            summary: "a.ts",
            detail: { window: { shownFrom: 1, shownTo: 9, total: 9 } },
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-r2");
    expect(el.textContent).toContain("a.ts");
    expect(el.textContent).toContain("L1–9/9");
    // The window is inline; read rows need no expander.
    expect(screen.queryByTestId("tool-expand-r2")).toBeNull();
  });
});

describe("same-window badge", () => {
  it("marks the second identical read window as same window", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "r3",
            tool: "read",
            summary: "f.ts",
            detail: { window: { shownFrom: 1, shownTo: 5, total: 9 } },
          }),
          row({
            callID: "r4",
            tool: "read",
            summary: "f.ts",
            detail: { window: { shownFrom: 1, shownTo: 5, total: 9 } },
          }),
        ]}
      />,
    );
    // First read: no badge.
    expect(screen.queryByTestId("same-window-r3")).toBeNull();
    // Second read with the same (path, window): badge present.
    const badge = screen.getByTestId("same-window-r4");
    expect(badge.textContent).toContain("same window");
  });

  it("does NOT mark a different window as same window", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "r5",
            tool: "read",
            summary: "f.ts",
            detail: { window: { shownFrom: 1, shownTo: 5, total: 9 } },
          }),
          row({
            callID: "r6",
            tool: "read",
            summary: "f.ts",
            detail: { window: { shownFrom: 6, shownTo: 9, total: 9 } },
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId("same-window-r5")).toBeNull();
    expect(screen.queryByTestId("same-window-r6")).toBeNull();
  });

  it("same-window badge also works in SegmentedBody interleave", () => {
    render(
      <SegmentedBody
        streaming={false}
        tools={[
          row({ callID: "s1", tool: "read", summary: "f", detail: { window: { shownFrom: 1, shownTo: 5, total: 9 } } }),
          row({ callID: "s2", tool: "read", summary: "f", detail: { window: { shownFrom: 1, shownTo: 5, total: 9 } } }),
        ]}
        segments={[
          { kind: "tool", callID: "s1" },
          { kind: "tool", callID: "s2" },
        ]}
      />,
    );
    expect(screen.queryByTestId("same-window-s1")).toBeNull();
    expect(screen.getByTestId("same-window-s2")).toBeDefined();
  });
});

describe("legacy-row degrade (pre step-1 servers)", () => {
  it("renders today's one-liner with no detail key and no expander", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({
            callID: "leg1",
            tool: "read",
            summary: "src/foo.ts",
            // No `detail` — legacy server.
          }),
        ]}
      />,
    );
    const el = screen.getByTestId("tool-row-leg1");
    expect(el.textContent).toContain("Read");
    expect(el.textContent).toContain("src/foo.ts");
    // No window math, no expander, no badge.
    expect(el.textContent).not.toContain("L");
    expect(screen.queryByTestId("tool-expand-leg1")).toBeNull();
    expect(screen.queryByTestId("same-window-leg1")).toBeNull();
  });

  it("bash legacy row shows the command (summary) only, no excerpt panel", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          row({ callID: "leg2", tool: "bash", summary: "rm -rf /x" }),
        ]}
      />,
    );
    expect(screen.getByTestId("tool-row-leg2").textContent).toContain("rm -rf /x");
    expect(screen.queryByTestId("tool-expand-leg2")).toBeNull();
  });
});

describe("shared ToolDetailMeta renderer (detail surface parity)", () => {
  it("renders read window rows for the detail surface", () => {
    const detail: ToolRowDetail = { window: { shownFrom: 11, shownTo: 30, total: 500, nextOffset: 31 } };
    render(<ToolDetailMeta tool="read" detail={detail} />);
    expect(screen.getByText(/lines 11–30 of 500/)).toBeDefined();
    expect(screen.getByText(/next offset 31/)).toBeDefined();
  });

  it("renders grep meta without match content", () => {
    const detail: ToolRowDetail = { pattern: "secret", path: "src", matchCount: 3 };
    render(<ToolDetailMeta tool="grep" detail={detail} />);
    expect(screen.getByText(/grep “secret”/)).toBeDefined();
    expect(screen.getByText(/3 matches/)).toBeDefined();
  });

  it("renders nothing when detail is absent (legacy detail surface)", () => {
    const { container } = render(<ToolDetailMeta tool="bash" detail={null} />);
    expect(container.textContent).toBe("");
  });
});
