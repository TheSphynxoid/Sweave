/**
 * Delegation tree builder (M1.9 Step 3).
 *
 * Pure function: ``buildDelegationTree(records) -> TreeNode[]``.
 * The Children page renders the tree; the tree builder is
 * stable + testable in isolation.
 *
 * A node is a root when ``parent_task_id`` is null OR points
 * to a delegation that's not in the supplied set (the orphan
 * case -- the parent was deleted; the child still exists in
 * the per-project store). Depth is 0 for roots, 1 for direct
 * children, etc.
 *
 * Sibling order: newest-first by ``created_at`` (descending --
 * M1.13 step 1, ruling 2026-09-10; the M1.9-era ascending order
 * pushed the newest work to the bottom of the tab). The tree
 * doesn't carry a depth limit;
 * the page's indent caps visually at a reasonable depth (the
 * actual delegation chain depth is bounded by the
 * DelegationManager -- default 2).
 */
import type { Delegation } from "@/types";

export interface TreeNode {
  record: Delegation;
  children: TreeNode[];
  /** Distance from the root (0-indexed). */
  depth: number;
}

export interface ProjectGroup {
  /** ``project_name`` of the group's roots; "" for the ghost/unknown group. */
  project: string;
  /** Top-level nodes (roots) belonging to this project. */
  nodes: TreeNode[];
  /** Total record count in the group (roots + descendants). */
  count: number;
  /** How many of those records are currently ``running``. */
  runningCount: number;
}

export function buildDelegationTree(records: Delegation[]): TreeNode[] {
  if (records.length === 0) return [];
  const byId = new Map<string, Delegation>();
  for (const r of records) byId.set(r.delegation_id, r);

  // Children map keyed by parent_task_id (string; '' = root).
  const childrenByParent = new Map<string, Delegation[]>();
  for (const r of records) {
    const key = r.parent_task_id ?? "";
    const arr = childrenByParent.get(key);
    if (arr) {
      arr.push(r);
    } else {
      childrenByParent.set(key, [r]);
    }
  }
  // Sort each sibling list newest-first (created_at descending).
  for (const arr of childrenByParent.values()) {
    arr.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  // Roots = records whose parent_task_id is null. A record whose
  // parent_task_id points to a delegation not in the set is
  // promoted to a root too (the orphan case -- the parent was
  // deleted; the child still exists in the per-project store).
  const roots = records
    .filter(
      (r) => r.parent_task_id === null || !byId.has(r.parent_task_id),
    )
    .slice();
  roots.sort((a, b) => b.created_at.localeCompare(a.created_at));

  // Children of orphan-parents (records whose parent_task_id
  // points to a delegation not in the set) were filed under the
  // orphan's id in childrenByParent; pull them out and re-file
  // them under the root (parent === null) so the root render
  // finds them.
  const orphanKeys: string[] = [];
  for (const r of records) {
    if (r.parent_task_id !== null && !byId.has(r.parent_task_id)) {
      orphanKeys.push(r.parent_task_id);
    }
  }
  for (const key of orphanKeys) {
    const moved = childrenByParent.get(key);
    if (moved && moved.length > 0) {
      const existing = childrenByParent.get("") ?? [];
      childrenByParent.set("", [...existing, ...moved]);
      childrenByParent.delete(key);
    }
  }

  function build(
    parent: Delegation | null,
    depth: number,
  ): TreeNode[] {
    const key = parent ? parent.delegation_id : "";
    const kids = childrenByParent.get(key) ?? [];
    return kids.map((k) => ({
      record: k,
      depth,
      children: build(k, depth + 1),
    }));
  }

  return build(null, 0);
}

/**
 * Flatten a tree to the list of nodes whose record is
 * ``needs_attention``. The Children tab's escalation lane
 * surfaces these at the top of the panel.
 */
export function findEscalatingNodes(nodes: TreeNode[]): TreeNode[] {
  const out: TreeNode[] = [];
  function walk(n: TreeNode) {
    if (n.record.needs_attention) out.push(n);
    for (const c of n.children) walk(c);
  }
  for (const n of nodes) walk(n);
  return out;
}

/**
 * Group the delegation tree by project (M1.13 step 1, ruling
 * 2026-09-10: "Group per project and make the page have a coherent
 * purpose"). The groups derive from the records themselves -- NOT
 * from the registered-projects endpoint -- so a deleted project's
 * children still render (under the ghost/unknown group).
 *
 * Ordering: the active project's group is pinned on top; the
 * remaining groups sort by their newest record (descending); the
 * ghost group (``project_name`` is null/unset) sinks to last, and
 * the Children page collapses it by default.
 */
export function groupDelegationTree(
  records: Delegation[],
  activeProject?: string | null,
): ProjectGroup[] {
  const tree = buildDelegationTree(records);
  const groups = new Map<string, ProjectGroup>();
  function get(project: string): ProjectGroup {
    const existing = groups.get(project);
    if (existing) return existing;
    const fresh: ProjectGroup = {
      project,
      nodes: [],
      count: 0,
      runningCount: 0,
    };
    groups.set(project, fresh);
    return fresh;
  }
  function countTree(n: TreeNode): { count: number; running: number } {
    let count = 1;
    let running = n.record.status === "running" ? 1 : 0;
    for (const c of n.children) {
      const sub = countTree(c);
      count += sub.count;
      running += sub.running;
    }
    return { count, running };
  }
  for (const node of tree) {
    const project = node.record.project_name ?? "";
    const group = get(project);
    group.nodes.push(node);
    const c = countTree(node);
    group.count += c.count;
    group.runningCount += c.running;
  }

  const newest = (g: ProjectGroup): string =>
    g.nodes.reduce(
      (max, n) => (n.record.created_at > max ? n.record.created_at : max),
      "",
    );
  const isGhost = (g: ProjectGroup) => g.project === "";
  const isActive = (g: ProjectGroup) =>
    !!activeProject && g.project === activeProject;
  return [...groups.values()].sort((a, b) => {
    if (isActive(a)) return -1;
    if (isActive(b)) return 1;
    if (isGhost(a)) return 1;
    if (isGhost(b)) return -1;
    return newest(b).localeCompare(newest(a));
  });
}
