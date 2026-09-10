/**
 * StatusPill tests — the honest-failure taxonomy (timeout ruling).
 *
 * A timeout-flavoured failed delegation renders a calm amber
 * "Timed out" pill; non-timeout failures keep the red "Failed"
 * treatment; done delegations surface elapsed runtime.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusPill } from "@/components/delegation/StatusPill";
import type { Delegation } from "@/types";

function delegation(overrides: Partial<Delegation>): Delegation {
  return {
    schema_version: 5,
    delegation_id: "d-1",
    task_id: "task-1",
    agent: "backend",
    model: "opencode/glm-5.3",
    task: "do a thing",
    status: "failed",
    created_at: "2026-09-09T10:00:00",
    updated_at: "2026-09-09T10:16:00",
    started_at: "2026-09-09T10:00:05",
    completed_at: null,
    parent_session_id: null,
    project_name: "shop",
    output: "",
    error: null,
    worktree_path: null,
    branch: null,
    pr_url: null,
    parent_task_id: null,
    manifest: null,
    depth: 1,
    chain_root_id: null,
    coordination_tokens: 0,
    kind: "task",
    needs_attention: false,
    ...overrides,
  };
}

describe("StatusPill", () => {
  it("timeout error → amber 'Timed out (after N min)' pill", () => {
    render(
      <StatusPill
        status={delegation({}).status}
        error="turn_timeout_exceeded_900s"
      />,
    );
    const pill = screen.getByTestId("status-pill-timed-out");
    expect(pill.textContent).toBe("⏱ Timed out (after 15 min)");
    expect(pill.className).toContain("amber");
  });

  it("non-timeout failure keeps the red Failed treatment", () => {
    render(
      <StatusPill status="failed" error="harness exited code 1" />,
    );
    const pill = screen.getByTestId("status-pill-failed");
    expect(pill.textContent).toBe("Failed");
    expect(pill.className).toContain("rose");
  });

  it("done delegation shows elapsed runtime when timestamps exist", () => {
    render(
      <StatusPill
        status="done"
        error={null}
        startedAt="2026-09-09T10:00:00"
        completedAt="2026-09-09T10:12:00"
      />,
    );
    expect(screen.getByTestId("status-pill-done")).toBeTruthy();
    expect(screen.getByTestId("status-runtime")).toBeTruthy();
    expect(screen.getByTestId("status-runtime").textContent).toBe("12 min");
  });

  it("done delegation without timestamps shows no runtime", () => {
    render(<StatusPill status="done" error={null} />);
    expect(screen.queryByTestId("status-runtime")).toBeNull();
  });

  it("timeout with unparseable seconds omits the minutes", () => {
    render(<StatusPill status="failed" error="turn_timeout_exceeded_" />);
    expect(screen.getByTestId("status-pill-timed-out").textContent).toBe(
      "⏱ Timed out",
    );
  });
});
