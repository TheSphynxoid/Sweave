/**
 * TurnTools render test (chat transparency).
 *
 * Read rows render as one-liners (`Read src/foo.ts`); edit rows
 * render the expandable diff card; status is visible per row.
 * SegmentedBody renders tool rows inline at their arrival positions
 * between thinking/text groups (the ordered timeline).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SegmentedBody, TurnTools } from "../Thread";

describe("TurnTools", () => {
  it("renders nothing when no tools ran", () => {
    const { container } = render(<TurnTools tools={[]} streaming={false} />);
    expect(container.textContent).toBe("");
  });

  it("renders a read row as a one-liner with its status", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          {
            callID: "c1",
            tool: "read",
            status: "completed",
            summary: "src/foo.ts",
            title: null,
            input: null,
            round: 0,
          },
        ]}
      />,
    );
    const row = screen.getByTestId("tool-row-c1");
    expect(row.textContent).toContain("Read");
    expect(row.textContent).toContain("src/foo.ts");
    expect(row.getAttribute("data-tool-status")).toBe("completed");
  });

  it("renders a running row with a live status", () => {
    render(
      <TurnTools
        streaming
        tools={[
          {
            callID: "c2",
            tool: "bash",
            status: "running",
            summary: "ls -la",
            title: null,
            input: null,
            round: 0,
          },
        ]}
      />,
    );
    const row = screen.getByTestId("tool-row-c2");
    expect(row.textContent).toContain("Bash");
    expect(row.getAttribute("data-tool-status")).toBe("running");
  });

  it("renders an edit row with the diff card", () => {
    render(
      <TurnTools
        streaming={false}
        tools={[
          {
            callID: "c3",
            tool: "edit",
            status: "completed",
            summary: "src/foo.ts",
            title: null,
            input: {
              filePath: "src/foo.ts",
              oldString: "aaa",
              newString: "bbb",
            },
            round: 0,
          },
        ]}
      />,
    );
    // The edit row mounts the diff card (not a plain one-liner).
    expect(screen.getByTestId("tool-row-c3")).toBeDefined();
  });
});

describe("SegmentedBody interleaving", () => {
  const tools = [
    {
      callID: "c1",
      tool: "read",
      status: "completed",
      summary: "src/foo.ts",
      title: null,
      input: null,
      round: 0,
    },
  ];

  it("renders think, tool, text in stream order", () => {
    const { container } = render(
      <SegmentedBody
        streaming={false}
        tools={tools}
        segments={[
          { kind: "thinking", text: "let me look" },
          { kind: "tool", callID: "c1" },
          { kind: "text", text: "found **it**" },
        ]}
      />,
    );
    expect(screen.getByTestId("thinking-block").textContent).toContain("let me look");
    const row = screen.getByTestId("tool-row-c1");
    expect(row.textContent).toContain("Read");
    // DOM order: thinking, tool row, answer.
    const order = Array.from(container.children).map((el) =>
      el.getAttribute("data-testid") === "thinking-block"
        ? "think"
        : el.querySelector("[data-testid='tool-row-c1']")
          ? "tool"
          : "text",
    );
    expect(order).toEqual(["think", "tool", "text"]);
  });

  it("drops tool markers whose row is missing instead of a hole", () => {
    const { container } = render(
      <SegmentedBody
        streaming={false}
        tools={[]}
        segments={[
          { kind: "text", text: "answer" },
          { kind: "tool", callID: "ghost" },
        ]}
      />,
    );
    expect(container.textContent).toContain("answer");
    expect(container.querySelector("[data-testid^='tool-row-']")).toBeNull();
  });
});
