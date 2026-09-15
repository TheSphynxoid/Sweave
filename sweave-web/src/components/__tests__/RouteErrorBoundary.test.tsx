/**
 * RouteErrorBoundary: a render throw in one route must not blank-screen
 * the app. Pins: healthy children pass through untouched; a throw is
 * contained to a labelled fallback (message + retry + chat escape);
 * retry recovers once the child heals.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RouteErrorBoundary } from "../RouteErrorBoundary";

let shouldThrow = false;

function MaybeBomb() {
  if (shouldThrow) throw new Error("kaboom-shape");
  return <p>recovered-content</p>;
}

describe("RouteErrorBoundary", () => {
  let errSpy: { mockRestore: () => void };

  beforeEach(() => {
    shouldThrow = false;
    errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    errSpy.mockRestore();
  });

  it("passes healthy children through with no fallback chrome", () => {
    render(
      <RouteErrorBoundary label="Usage">
        <MaybeBomb />
      </RouteErrorBoundary>,
    );
    expect(screen.queryByTestId("route-error")).toBeNull();
    expect(screen.getByText("recovered-content")).toBeDefined();
  });

  it("catches a render throw and recovers on retry", () => {
    shouldThrow = true;
    render(
      <RouteErrorBoundary label="Usage">
        <MaybeBomb />
      </RouteErrorBoundary>,
    );
    const fallback = screen.getByTestId("route-error");
    expect(fallback.textContent).toContain("Something went wrong in Usage");
    expect(fallback.textContent).toContain("kaboom-shape");
    expect(
      fallback.querySelector('a[href="/chat"]')?.textContent,
    ).toContain("Back to chat");
    // Heal the child, then retry: the surface recovers in place.
    shouldThrow = false;
    fireEvent.click(screen.getByText("Try again"));
    expect(screen.getByText("recovered-content")).toBeDefined();
    expect(screen.queryByTestId("route-error")).toBeNull();
  });
});
