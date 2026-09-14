/**
 * Tooltip portal regression test.
 *
 * The flicker: tooltip content rendered inline inside message cards /
 * the composer, both of which carry `backdrop-blur` — a containing
 * block for fixed descendants. The tip then overflowed its card,
 * grew the page, shifted the button out from under the cursor, and
 * hover-looped (retry/copy/stop buttons). Portaling to document.body
 * keeps the tip in viewport coordinates with zero layout impact.
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "../tooltip";

function Tip() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" data-testid="tip-trigger">
            x
          </button>
        </TooltipTrigger>
        <TooltipContent>tip text</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

describe("TooltipContent portal", () => {
  it("renders nothing until hover", () => {
    render(<Tip />);
    expect(screen.queryByText("tip text")).toBeNull();
  });

  it("portals the open tip to document.body (never inside the trigger tree)", async () => {
    const { container } = render(
      <div data-testid="trigger-tree">
        <Tip />
      </div>
    );
    await act(async () => {
      fireEvent.mouseEnter(screen.getByTestId("tip-trigger"));
    });
    const tip = screen.getByText("tip text");
    // Portaled out: not a descendant of the trigger's subtree.
    const tree = screen.getByTestId("trigger-tree");
    expect(tree.contains(tip)).toBe(false);
    expect(document.body.contains(tip)).toBe(true);
    // Fixed in viewport coordinates: cannot grow the page.
    expect(tip.style.position).toBe("fixed");
    expect(container.contains(tip)).toBe(false);
  });

  it("unmounts the tip on leave (no residue)", async () => {
    render(<Tip />);
    await act(async () => {
      fireEvent.mouseEnter(screen.getByTestId("tip-trigger"));
    });
    expect(screen.getByText("tip text")).toBeTruthy();
    await act(async () => {
      fireEvent.mouseLeave(screen.getByTestId("tip-trigger"));
    });
    expect(screen.queryByText("tip text")).toBeNull();
  });
});
