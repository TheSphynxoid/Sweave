/**
 * Plan board builder tests (TRACKING_PLAN Phase A).
 *
 * Pins: status→column mapping, newest-first order, the BOARD_CAP
 * volume guard with honest total/shown counts, and the bugs-lane
 * merge rule (failed first, then needs_attention, deduped by id).
 */
import { describe, it, expect } from "vitest";
import { buildPlanBoard, BOARD_CAP, PLAN_COLUMNS } from "../board";
import type { Delegation } from "@/types";

function delegation(overrides: Partial<Delegation>): Delegation {
  return {
    schema_version: 5,
    delegation_id: "d-1",
    task_id: "task-1",
    agent: "backend",
    model: "opencode/glm-5.3",
    task: "do a thing",
    status: "queued",
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

describe("buildPlanBoard columns", () => {
  it("covers every DelegationStatus exactly once", () => {
    expect([...PLAN_COLUMNS].sort()).toEqual(
      ["done", "failed", "queued", "review", "running"].sort(),
    );
  });

  it("files each record into its status column, newest-first", () => {
    const board = buildPlanBoard([
      delegation({ delegation_id: "old", status: "done", created_at: "2026-09-08T10:00:00" }),
      delegation({ delegation_id: "new", status: "done", created_at: "2026-09-10T10:00:00" }),
      delegation({ delegation_id: "r1", status: "running" }),
    ]);
    const done = board.columns.find((c) => c.status === "done")!;
    expect(done.items.map((r) => r.delegation_id)).toEqual(["new", "old"]);
    expect(board.columns.find((c) => c.status === "running")!.items).toHaveLength(1);
    expect(board.columns.find((c) => c.status === "queued")!.items).toEqual([]);
  });

  it("returns an empty board for no records", () => {
    const board = buildPlanBoard([]);
    expect(board.total).toBe(0);
    expect(board.shown).toBe(0);
    expect(board.bugs).toEqual([]);
    expect(board.columns.every((c) => c.items.length === 0)).toBe(true);
  });
});

describe("buildPlanBoard volume guard", () => {
  it("caps at BOARD_CAP newest with honest counts", () => {
    const records = Array.from({ length: BOARD_CAP + 50 }, (_, i) =>
      delegation({
        delegation_id: `d-${i}`,
        created_at: `2026-09-${String((i % 28) + 1).padStart(2, "0")}T10:${String(i % 60).padStart(2, "0")}:00`,
      }),
    );
    const board = buildPlanBoard(records);
    expect(board.total).toBe(BOARD_CAP + 50);
    expect(board.shown).toBe(BOARD_CAP);
    expect(board.columns.flatMap((c) => c.items)).toHaveLength(BOARD_CAP);
  });
});

describe("buildPlanBoard bugs lane", () => {
  it("orders failed first, then needs_attention, deduped by id", () => {
    const board = buildPlanBoard([
      delegation({ delegation_id: "ask", status: "running", needs_attention: true }),
      delegation({ delegation_id: "fail", status: "failed", needs_attention: true }),
      delegation({ delegation_id: "plain-fail", status: "failed" }),
      delegation({ delegation_id: "ok", status: "done" }),
    ]);
    // failed (newest-first among themselves) come before the
    // needs_attention non-failed record; the failed+flagged record
    // appears once, in the failed block.
    expect(board.bugs.map((r) => r.delegation_id)).toEqual([
      "fail",
      "plain-fail",
      "ask",
    ]);
  });
});
