/**
 * Tests for the children tree builder (M1.9 Step 3).
 *
 * The Children page renders a tree of delegations keyed by
 * ``parent_task_id``. The tree builder is a pure function:
 * ``buildDelegationTree(records) -> TreeNode[]``. The tests
 * pin:
 *
 *   * Top-level nodes (parent_task_id === null) are roots.
 *   * Children are nested by delegation_id == parent_task_id.
 *   * Depth is the distance from the root (0-indexed).
 *   * The escalation lane flag (``needs_attention``) propagates
 *     up the tree (a child escalates the parent context; the
 *     parent record is the row the user clicks for the answer
 *     button when a deeper delegate escalates).
 *   * Empty / single-node / multi-branch / deep chains.
 */
import { describe, it, expect } from "vitest";
import {
  buildDelegationTree,
  findEscalatingNodes,
  type TreeNode,
} from "../tree";
import type { Delegation } from "@/types";

function d(
  id: string,
  status: Delegation["status"],
  parent: string | null = null,
  extras: Partial<Delegation> = {},
): Delegation {
  return {
    schema_version: 5,
    delegation_id: id,
    task_id: `t-${id}`,
    agent: "x",
    model: "",
    task: `task ${id}`,
    status,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    parent_session_id: null,
    project_name: "p",
    output: "",
    error: null,
    worktree_path: null,
    branch: null,
    pr_url: null,
    parent_task_id: parent,
    manifest: null,
    depth: 0,
    chain_root_id: null,
    coordination_tokens: 0,
    kind: "task",
    needs_attention: false,
    ...extras,
  };
}

describe("buildDelegationTree: empty", () => {
  it("returns an empty array", () => {
    expect(buildDelegationTree([])).toEqual([]);
  });
});

describe("buildDelegationTree: single node", () => {
  it("returns a single root", () => {
    const tree = buildDelegationTree([d("a", "running")]);
    expect(tree).toHaveLength(1);
    expect(tree[0].record.delegation_id).toBe("a");
    expect(tree[0].children).toEqual([]);
    expect(tree[0].depth).toBe(0);
  });
});

describe("buildDelegationTree: parent -> child", () => {
  it("nests a child under its parent", () => {
    const tree = buildDelegationTree([
      d("a", "running"),
      d("b", "running", "a"),
    ]);
    expect(tree).toHaveLength(1);
    expect(tree[0].record.delegation_id).toBe("a");
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children[0].record.delegation_id).toBe("b");
    expect(tree[0].children[0].depth).toBe(1);
  });
});

describe("buildDelegationTree: multi-branch", () => {
  it("renders siblings under a common parent", () => {
    const tree = buildDelegationTree([
      d("a", "running"),
      d("b", "running", "a"),
      d("c", "running", "a"),
    ]);
    expect(tree).toHaveLength(1);
    expect(tree[0].children).toHaveLength(2);
    const ids = tree[0].children.map((c) => c.record.delegation_id).sort();
    expect(ids).toEqual(["b", "c"]);
  });
});

describe("buildDelegationTree: deep chain", () => {
  it("nests three levels deep", () => {
    const tree = buildDelegationTree([
      d("a", "running"),
      d("b", "running", "a"),
      d("c", "running", "b"),
    ]);
    expect(tree[0].children[0].children[0].record.delegation_id).toBe("c");
    expect(tree[0].children[0].children[0].depth).toBe(2);
  });
});

describe("buildDelegationTree: orphan handling", () => {
  it("treats a child whose parent is missing as a root", () => {
    const tree = buildDelegationTree([
      d("a", "running"),
      d("orphan", "running", "missing-parent"),
    ]);
    expect(tree).toHaveLength(2);
    const ids = tree.map((t) => t.record.delegation_id).sort();
    expect(ids).toEqual(["a", "orphan"]);
  });
});

describe("buildDelegationTree: needs_attention", () => {
  it("carries the needs_attention flag through", () => {
    const tree = buildDelegationTree([
      d("a", "running", null, { needs_attention: false }),
      d("b", "review", "a", { needs_attention: true }),
    ]);
    expect(tree[0].record.needs_attention).toBe(false);
    expect(tree[0].children[0].record.needs_attention).toBe(true);
  });
});

describe("buildDelegationTree: stable sort", () => {
  it("sorts siblings by created_at ascending", () => {
    const tree = buildDelegationTree([
      d("root", "running", null, { created_at: "2026-01-01T00:00:02Z" }),
      d("older", "running", "root", { created_at: "2026-01-01T00:00:00Z" }),
      d("newer", "running", "root", { created_at: "2026-01-01T00:00:01Z" }),
    ]);
    const ids = tree[0].children.map((c) => c.record.delegation_id);
    expect(ids).toEqual(["older", "newer"]);
  });
});

describe("findEscalatingNodes", () => {
  it("returns every node with needs_attention=true (flat list)", () => {
    const tree: TreeNode[] = [
      {
        record: d("a", "running", null, { needs_attention: true }),
        children: [
          {
            record: d("b", "review", "a", { needs_attention: false }),
            children: [],
            depth: 1,
          },
        ],
        depth: 0,
      },
      {
        record: d("c", "review", null, { needs_attention: true }),
        children: [],
        depth: 0,
      },
    ];
    const out = findEscalatingNodes(tree);
    expect(out.map((n) => n.record.delegation_id).sort()).toEqual(["a", "c"]);
  });
});
