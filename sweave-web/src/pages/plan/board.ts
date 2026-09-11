/**
 * Plan board builder (TRACKING_PLAN Phase A).
 *
 * Pure function: ``buildPlanBoard(records) -> PlanBoard``. The Plan page
 * renders Kanban + Table + Bugs lane from it; the builder is stable +
 * testable in isolation (LiveTree/tree.ts pattern).
 *
 * Source of truth is the Delegation record list (existing
 * ``GET /api/delegations`` shape) — NOT trace ``todo`` parts: a
 * full-corpus scan (2026-09-11) found zero ``todo`` tool parts across
 * 6,396 traces, so a trace-projected todo view would ship empty.
 *
 * Columns follow ``DelegationStatus`` order (queued → running → review
 * → done → failed). The Bugs lane merges two sources with one rule:
 * ``failed`` first (newest-first), then ``needs_attention`` non-failed
 * records, deduplicated by ``delegation_id`` — a failed record with
 * its flag set appears once, in the failed position.
 *
 * Volume guard (TRACKING_PLAN risk): the board caps at BOARD_CAP newest
 * records (``created_at`` descending) and reports ``total`` vs
 * ``shown`` so the page can print an honest "showing N of M" note.
 * Detail/escalation endpoints stay on-demand (row click), never per-row.
 */
import type { Delegation, DelegationStatus } from "@/types";

export const PLAN_COLUMNS: readonly DelegationStatus[] = [
  "queued",
  "running",
  "review",
  "done",
  "failed",
];

/** Newest-first client-side cap (TRACKING_PLAN volume guard). */
export const BOARD_CAP = 200;

export interface PlanColumn {
  status: DelegationStatus;
  items: Delegation[];
}

export interface PlanBoard {
  columns: PlanColumn[];
  /** Bugs lane: failed first, then needs_attention, deduped by id. */
  bugs: Delegation[];
  /** Total records supplied (pre-cap). */
  total: number;
  /** Records actually placed on the board (post-cap). */
  shown: number;
}

function byNewest(a: Delegation, b: Delegation): number {
  return b.created_at.localeCompare(a.created_at);
}

export function buildPlanBoard(records: Delegation[]): PlanBoard {
  const total = records.length;
  const shown = [...records].sort(byNewest).slice(0, BOARD_CAP);

  const columns: PlanColumn[] = PLAN_COLUMNS.map((status) => ({
    status,
    items: shown.filter((r) => r.status === status),
  }));

  // Bugs lane: failed first (newest-first), then needs_attention
  // records that are NOT failed, deduplicated by delegation_id.
  const seen = new Set<string>();
  const bugs: Delegation[] = [];
  for (const r of shown) {
    if (r.status !== "failed") continue;
    if (seen.has(r.delegation_id)) continue;
    seen.add(r.delegation_id);
    bugs.push(r);
  }
  for (const r of shown) {
    if (!r.needs_attention || r.status === "failed") continue;
    if (seen.has(r.delegation_id)) continue;
    seen.add(r.delegation_id);
    bugs.push(r);
  }

  return { columns, bugs, total, shown: shown.length };
}
