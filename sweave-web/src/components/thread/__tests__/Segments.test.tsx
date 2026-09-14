/**
 * SegmentedBody order test (interleave fidelity).
 *
 * Think, text, think must render in stream order — two thinking
 * blocks around the answer — instead of collapsing to one Thinking
 * blob + one answer.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SegmentedBody } from "../Thread";

describe("SegmentedBody", () => {
  it("renders think/text/think in stream order", () => {
    render(
      <SegmentedBody
        streaming={false}
        segments={[
          { kind: "thinking", text: "first thought" },
          { kind: "text", text: "middle **answer**" },
          { kind: "thinking", text: "second thought" },
        ]}
      />,
    );
    const blocks = screen.getAllByTestId("thinking-block");
    expect(blocks).toHaveLength(2);
    expect(blocks[0].textContent).toContain("first thought");
    expect(blocks[1].textContent).toContain("second thought");
    // The answer renders between the two thinking blocks in DOM order.
    const container = blocks[0].parentElement!;
    const order = Array.from(container.children).map((el) =>
      el.getAttribute("data-testid") === "thinking-block" ? "think" : "text",
    );
    expect(order).toEqual(["think", "text", "think"]);
    expect(container.textContent).toContain("answer");
  });

  it("merges contiguous same-kind runs", () => {
    render(
      <SegmentedBody
        streaming={false}
        segments={[
          { kind: "thinking", text: "a" },
          { kind: "thinking", text: "b" },
          { kind: "text", text: "c" },
        ]}
      />,
    );
    expect(screen.getAllByTestId("thinking-block")).toHaveLength(1);
    expect(screen.getByTestId("thinking-block").textContent).toContain("ab");
  });
});
