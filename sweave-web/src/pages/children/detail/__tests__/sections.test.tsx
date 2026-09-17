/**
 * TOOL_CARDS step 2b — detail-view fold-in tests.
 *
 * Pins (plan item 5, done-gate):
 *  - bash no-dupe command: the `BashTool` card shows `$ cmd`; the
 *    shared `ToolDetailMeta` is mounted with `hideCommand` so it does
 *    NOT repeat the command row (keeps exit + truncated).
 *  - grep/glob/git/todo curated audit row (F6) + collapsible raw-JSON
 *    toggle (forensics only); default surface stays scan-safe.
 *  - edit camelCase (filePath/oldString/newString/content) routes to the
 *    `EditTool` diff card (snake_case-only used to fall through to the
 *    raw `AgentToolCard` dump).
 *  - write overwrite green/red: `detail.old_capture` (pre-write bytes)
 *    becomes the diff old-side; both old + new render.
 *  - legacy no-detail degrade: rows with no `detail` and no curated edit
 *    shape fall to the raw `AgentToolCard`; curated tools degrade to an
 *    empty audit row + raw toggle rather than a JSON wall.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// jsdom has no constructable stylesheets (`sheet.replaceSync`), which the
// real `@pierre/diffs` web component needs. Mock it so the diff card can
// be exercised in the test env; the stub renders the old/new file
// contents verbatim so routing + overwrite (old_capture) assertions hold.
vi.mock("@pierre/diffs/react", () => ({
  MultiFileDiff: ({ oldFile, newFile }: any) => {
    if (!oldFile && !newFile) return null;
    return (
      <div data-testid="multi-file-diff">
        <pre data-testid="diff-old">{typeof oldFile?.contents === "string" ? oldFile.contents : ""}</pre>
        <pre data-testid="diff-new">{typeof newFile?.contents === "string" ? newFile.contents : ""}</pre>
      </div>
    );
  },
}));

import { ToolTimelineRow } from "../sections";
import { ToolDetailMeta } from "@/components/thread/ToolDetailMeta";
import type { ToolTimelineEntry } from "@/types";

function entry(
  partial: Partial<ToolTimelineEntry> & { callID: string; tool: string },
): ToolTimelineEntry {
  return {
    status: "completed",
    output: undefined,
    error: null,
    title: null,
    input: undefined,
    states: [],
    started_at: null,
    detail: null,
    ...partial,
  };
}

describe("ToolDetailMeta hideCommand (item 1)", () => {
  it("hides the $ command row but keeps exit when hideCommand is set", () => {
    render(
      <ToolDetailMeta tool="bash" detail={{ command: "ls -la", exit: 0 }} hideCommand />,
    );
    // Command row suppressed.
    expect(screen.queryByText("$ ls -la")).toBeNull();
    // Exit code still rendered (audit line stays complete).
    expect(screen.getByText(/exit 0/)).toBeDefined();
  });

  it("shows the $ command row by default (chat surface)", () => {
    render(<ToolDetailMeta tool="bash" detail={{ command: "ls -la", exit: 0 }} />);
    expect(screen.getByText("$ ls -la")).toBeDefined();
    expect(screen.getByText(/exit 0/)).toBeDefined();
  });
});

describe("bash no-dupe command (item 1, fold-in)", () => {
  it("renders the command exactly once across BashTool + ToolDetailMeta", () => {
    const { container } = render(
      <ToolTimelineRow
        tool={entry({
          callID: "b1",
          tool: "bash",
          input: { command: "npm test" },
          detail: { command: "npm test", exit: 0 },
        })}
      />,
    );
    // The BashTool card renders the command in its own span; the
    // hidden-command ToolDetailMeta must NOT emit a second command span.
    const spans = container.querySelectorAll("span");
    let cmdCount = 0;
    spans.forEach((el) => {
      if (el.textContent === "npm test") cmdCount++;
    });
    expect(cmdCount).toBe(1);
    // Exit code present (rendered by the hidden-command meta).
    expect(container.textContent).toContain("exit 0");
  });
});

describe("grep/glob/git/todo curated row + raw toggle (item 2)", () => {
  it("grep shows a curated audit row (no content) and a collapsed raw toggle", () => {
    render(
      <ToolTimelineRow
        tool={entry({
          callID: "g1",
          tool: "grep",
          detail: { pattern: "secret", path: "src", matchCount: 3 },
        })}
      />,
    );
    expect(screen.getByTestId("curated-g1")).toBeDefined();
    expect(screen.getByText(/grep “secret”/)).toBeDefined();
    expect(screen.getByText(/3 matches/)).toBeDefined();
    // Raw JSON hidden until toggled.
    expect(screen.queryByTestId("agent-tool-card")).toBeNull();
    const toggle = screen.getByTestId("curated-raw-toggle-g1");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(screen.getByTestId("agent-tool-card")).toBeDefined();
  });

  it("glob + git + todo render curated rows", () => {
    const { container } = render(
      <>
        <ToolTimelineRow tool={entry({ callID: "gl1", tool: "glob", detail: { pattern: "**/*.ts", count: 7 } })} />
        <ToolTimelineRow tool={entry({ callID: "gi1", tool: "git", detail: { verb: "log", args: ["-n", "3"] } })} />
        <ToolTimelineRow tool={entry({ callID: "t1", tool: "todo", detail: { titles: ["a", "b"] } })} />
      </>,
    );
    expect(container.textContent).toContain("**/*.ts");
    expect(container.textContent).toContain("7 paths");
    expect(container.textContent).toContain("git log");
    expect(container.textContent).toContain("-n 3");
    expect(container.textContent).toContain("a");
  });

  it("curated tool with no detail degrades to an empty audit row + raw toggle", () => {
    render(
      <ToolTimelineRow
        tool={entry({ callID: "g2", tool: "grep", input: { pattern: "x" }, output: "matches" })}
      />,
    );
    // No curated meta text (no pattern in detail), but raw toggle is
    // available for forensics.
    expect(screen.getByTestId("curated-raw-toggle-g2")).toBeDefined();
    expect(screen.queryByTestId("agent-tool-card")).toBeNull();
    fireEvent.click(screen.getByTestId("curated-raw-toggle-g2"));
    expect(screen.getByTestId("agent-tool-card")).toBeDefined();
  });
});

describe("edit camelCase routes to diff card (item 3)", () => {
  it("edit with camelCase filePath/oldString/newString renders EditTool (not raw dump)", () => {
    render(
      <ToolTimelineRow
        tool={entry({
          callID: "e1",
          tool: "edit",
          input: { filePath: "src/x.ts", oldString: "a", newString: "b" },
        })}
      />,
    );
    // EditTool (mocked diff) shows the new side; the raw AgentToolCard
    // is NOT the default surface for a known edit shape.
    expect(screen.getByTestId("diff-new").textContent).toContain("b");
    expect(screen.queryByTestId("agent-tool-card")).toBeNull();
    expect(screen.queryByTestId("curated-e1")).toBeNull();
  });

  it("write with camelCase filePath/content renders EditTool create card", () => {
    const { container } = render(
      <ToolTimelineRow
        tool={entry({
          callID: "w1",
          tool: "write",
          input: { filePath: "src/new.ts", content: "const a = 1;\n" },
        })}
      />,
    );
    expect(container.textContent).toContain("new.ts");
    expect(container.textContent).toContain("Created");
    expect(screen.queryByTestId("agent-tool-card")).toBeNull();
  });
});

describe("write overwrite green/red with old_capture (item 3)", () => {
  it("renders both old (overwrite) and new content in the diff", () => {
    render(
      <ToolTimelineRow
        tool={entry({
          callID: "w2",
          tool: "write",
          input: { filePath: "src/over.ts", content: "new line b\nnew line c\n" },
          detail: {
            mode: "overwrite",
            old_capture: "old line a\n",
            linesRemoved: 1,
            linesAdded: 2,
          },
        })}
      />,
    );
    // Old-side (pre-write bytes) and new-side both render → real diff.
    expect(screen.getByTestId("diff-old").textContent).toContain("old line a");
    expect(screen.getByTestId("diff-new").textContent).toContain("new line b");
    expect(screen.getByTestId("diff-new").textContent).toContain("new line c");
    // No raw dump by default (it's the diff card, not AgentToolCard).
    expect(screen.queryByTestId("agent-tool-card")).toBeNull();
  });
});

describe("legacy no-detail degrade (item 3 fallback)", () => {
  it("unknown tool with no detail falls to the raw AgentToolCard dump", () => {
    render(
      <ToolTimelineRow
        tool={entry({
          callID: "u1",
          tool: "weird_tool",
          input: { foo: "bar" },
          output: { baz: 1 },
        })}
      />,
    );
    // Raw card is the default surface for an unrecognized tool.
    expect(screen.getByTestId("agent-tool-card")).toBeDefined();
  });

  it("edit with no recognizable input + no detail falls to raw card", () => {
    render(
      <ToolTimelineRow
        tool={entry({ callID: "u2", tool: "edit", input: { note: "hi" } })}
      />,
    );
    // No filePath/oldString/newString/path → not an edit shape → raw.
    expect(screen.getByTestId("agent-tool-card")).toBeDefined();
  });
});
