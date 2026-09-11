/**
 * LiveTree honest-failure states (M1.13 step 2, 2026-09-10 ruling).
 *
 * A wire-dead child arrives as a ``failed`` record carrying the
 * real error text ([chat error: ...]). Failed CHILDREN render an
 * amber pill (never red) plus a short error note on the row.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LiveTree } from "../LiveTree";
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
    started_at: null,
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
    depth: 0,
    chain_root_id: null,
    coordination_tokens: 0,
    kind: "task",
    needs_attention: false,
    ...overrides,
  };
}

function baseList(records: Delegation[]): Delegation[] {
  return records;
}

describe("LiveTree: honest failure states", () => {
  it("renders a wire-dead child amber (not red) with its error note", () => {
    render(
      <LiveTree
        delegations={baseList([
          delegation({
            delegation_id: "w1",
            status: "failed",
            error: "[chat error: APIError: 401 upstream rejected]",
          }),
        ])}
        onOpen={() => {}}
      />,
    );
    const pill = screen.getByTestId("status-pill-failed");
    expect(pill.className).toContain("amber");
    expect(pill.className).not.toContain("rose");
    const note = screen.getByTestId("row-error-w1");
    expect(note.textContent).toContain("APIError");
  });

  it("truncates a long error note", () => {
    render(
      <LiveTree
        delegations={baseList([
          delegation({
            delegation_id: "w2",
            status: "failed",
            error: "x".repeat(300),
          }),
        ])}
        onOpen={() => {}}
      />,
    );
    const note = screen.getByTestId("row-error-w2");
    expect((note.textContent || "").length).toBeLessThanOrEqual(123);
  });

  it("failed without error text shows the amber pill but no note", () => {
    render(
      <LiveTree
        delegations={baseList([
          delegation({ delegation_id: "w3", status: "failed", error: null }),
        ])}
        onOpen={() => {}}
      />,
    );
    const pill = screen.getByTestId("status-pill-failed");
    expect(pill.className).toContain("amber");
    expect(screen.queryByTestId("row-error-w3")).toBeNull();
  });
});
