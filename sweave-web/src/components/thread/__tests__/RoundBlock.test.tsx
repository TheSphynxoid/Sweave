/**
 * RoundBlock tests (multi-message turns, 2026-09-11).
 *
 * Intermediate round messages render collapsed but present: the
 * round label + content preview show without expanding, click
 * expands the full narration in place. (No jest-dom in this repo —
 * assertions use textContent / null queries like the other suites.)
 */
import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RoundBlock } from "@/components/thread/Thread";

describe("RoundBlock", () => {
  it("renders collapsed with the round label + preview", () => {
    render(
      <RoundBlock round={0} preview="Dispatching to backend for the font work">
        <p>Dispatching to backend for the font work, full text here.</p>
      </RoundBlock>,
    );
    const block = screen.getByTestId("round-block");
    expect(block.textContent).toContain("Round 1");
    expect(block.textContent).toContain("Dispatching to backend");
    expect(screen.queryByText("Dispatching to backend for the font work, full text here.")).toBeNull();
  });

  it("expands the narration on click", () => {
    render(
      <RoundBlock round={1} preview="Backend finished">
        <p>Backend finished the whole thing.</p>
      </RoundBlock>,
    );
    expect(screen.getByTestId("round-block").textContent).toContain("Round 2");
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("Backend finished the whole thing.")).not.toBeNull();
  });
});
