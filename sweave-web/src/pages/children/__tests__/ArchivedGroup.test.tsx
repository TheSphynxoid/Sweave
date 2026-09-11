/**
 * Archived group (M1.13 step 5, ruling 2026-09-11: stats preserved,
 * no clutter).
 *
 * The default Children view shows ONLY live records; the archived
 * surface is ONE compact collapsed row at the bottom. Expanding it
 * shows per-project AGGREGATE summaries (counts, by-status, by-kind)
 * -- never per-delegation rows. Aggregates carry no token sums
 * (the backend aggregate shape does not expose one; nothing invented).
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ArchivedGroup } from "../ArchivedGroup";
import { LiveTree } from "../LiveTree";
import type { ArchivedProjectSummary, Delegation } from "@/types";

function delegation(overrides: Partial<Delegation>): Delegation {
  return {
    schema_version: 5,
    delegation_id: "d-1",
    task_id: "task-1",
    agent: "backend",
    model: "opencode/glm-5.3",
    task: "do a thing",
    status: "done",
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
    archived: false,
    archived_at: null,
    ...overrides,
  };
}

function aggregate(overrides: Partial<ArchivedProjectSummary>): ArchivedProjectSummary {
  return {
    project_name: "old-app",
    workdir: null,
    total: 3,
    by_status: { done: 2, failed: 1 },
    by_kind: { chat: 1, task: 2 },
    archived_at: "2026-09-11T02:00:00",
    last_created_at: "2026-09-10T09:00:00",
    source: "index",
    ...overrides,
  };
}

describe("ArchivedGroup", () => {
  it("renders nothing when the archive is empty", () => {
    const { container } = render(<ArchivedGroup archivedProjects={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("shows ONE compact collapsed row by default (no aggregate cards, no rows)", () => {
    render(
      <ArchivedGroup
        archivedProjects={[
          aggregate({ project_name: "old-app", total: 3 }),
          aggregate({ project_name: "vault", total: 2 }),
        ]}
      />,
    );
    const row = screen.getByTestId("archived-group-row");
    expect(row.textContent).toContain("Archived");
    expect(row.textContent).toContain("2 projects");
    expect(row.textContent).toContain("5 delegations");
    // Collapsed: no per-project aggregates are visible yet.
    expect(
      screen.queryByTestId("archived-project-old-app"),
    ).toBeNull();
  });

  it("expands to per-project aggregate summaries (counts, NOT delegation rows)", () => {
    render(
      <ArchivedGroup
        archivedProjects={[
          aggregate({ project_name: "old-app", total: 3 }),
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId("archived-group-toggle"));
    const card = screen.getByTestId("archived-project-old-app");
    expect(card.textContent).toContain("3");
    expect(card.textContent).toContain("done 2");
    expect(card.textContent).toContain("failed 1");
    expect(card.textContent).toContain("chat 1");
    expect(card.textContent).toContain("task 2");
    // No per-delegation rows ever render inside the archived group.
    expect(screen.queryByTestId(/tree-row-/)).toBeNull();
  });

  it("live groups render unchanged ahead of the archived group", () => {
    render(
      <>
        <LiveTree
          delegations={[delegation({ delegation_id: "d-live" })]}
          onOpen={() => {}}
        />
        <ArchivedGroup archivedProjects={[aggregate({ total: 2 })]} />
      </>,
    );
    expect(screen.getByTestId("tree-row-d-live")).not.toBeNull();
    // Archived stays collapsed; the live tree is untouched.
    expect(screen.getByTestId("archived-group-row").textContent).toContain(
      "1 projects",
    );
    expect(screen.queryByTestId("archived-project-old-app")).toBeNull();
  });
});
