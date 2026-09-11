/**
 * Plan page (TRACKING_PLAN Phase A).
 *
 * Read-only board over the Delegation records: Kanban columns by
 * status, a table of the same records, and a bugs lane (failed first,
 * then needs_attention, deduped — see `plan/board.ts`). No backend
 * change: the page reads the existing ``GET /api/delegations`` shape
 * (same query key as Children, so both surfaces share one cache) and
 * opens the shared M1.9 DetailView modal on row click.
 *
 * Trace ``todo`` parts are deliberately NOT a source here: a
 * full-corpus scan (2026-09-11) found zero across 6,396 traces.
 * User-created tickets land in Phase B.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { DetailView } from "@/pages/children/DetailView";
import { KindPill, StatusPill } from "@/pages/children/LiveTree";
import { buildPlanBoard, PLAN_COLUMNS } from "@/pages/plan/board";
import { formatRelativeTime, truncate, cn } from "@/utils/cn";
import type { Delegation } from "@/types";

type PlanView = "kanban" | "table";

export function PlanPage() {
  const { activeProject } = useApp();
  const { subscribe } = useWS();
  const qc = useQueryClient();
  const [view, setView] = useState<PlanView>("kanban");
  const [openId, setOpenId] = useState<string | null>(null);

  // Same query key as Children: one cache, both surfaces stay fresh.
  const { data: delegations = [] } = useQuery<Delegation[]>({
    queryKey: ["delegations"],
    queryFn: () => api.listDelegations(),
  });

  // WS pulse: the Children-page pattern (status + escalation events
  // invalidate; rows re-render from changed data only).
  useEffect(() => {
    const off = subscribe("delegation.status_changed", () => {
      void qc.invalidateQueries({ queryKey: ["delegations"] });
    });
    const offEsc = subscribe("specialist.escalated", () => {
      void qc.invalidateQueries({ queryKey: ["delegations"] });
    });
    const offRes = subscribe("specialist.escalation_resolved", () => {
      void qc.invalidateQueries({ queryKey: ["delegations"] });
    });
    return () => {
      off();
      offEsc();
      offRes();
    };
  }, [subscribe, qc]);

  const board = useMemo(() => buildPlanBoard(delegations), [delegations]);

  if (!activeProject) {
    return (
      <div className="p-6" data-testid="plan-page">
        <p className="text-sm text-muted-foreground">
          Activate a project to see its plan board.
        </p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4" data-testid="plan-page">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Plan</h1>
          <p className="text-sm text-muted-foreground">
            Board over delegations · read-only (tickets land in Phase B)
            {board.total > board.shown && (
              <span data-testid="plan-cap-note">
                {" · "}showing {board.shown} of {board.total}
              </span>
            )}
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border border-border p-1" role="tablist" aria-label="Plan view">
          {(["kanban", "table"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              data-testid={`plan-view-${v}`}
              onClick={() => setView(v)}
              className={cn(
                "rounded-md px-3 py-1 text-sm capitalize",
                view === v
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {v}
            </button>
          ))}
        </div>
      </header>

      {board.bugs.length > 0 && (
        <section aria-label="Needs attention" data-testid="plan-bugs-lane" className="rounded-lg border border-amber-500/30 p-3 space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-300">
            Needs attention ({board.bugs.length})
          </h2>
          <ul className="space-y-1">
            {board.bugs.map((r) => (
              <PlanRow key={r.delegation_id} record={r} onOpen={setOpenId} />
            ))}
          </ul>
        </section>
      )}

      {board.total === 0 ? (
        <div
          data-testid="plan-empty"
          className="text-sm text-muted-foreground p-4 border border-dashed border-border rounded"
        >
          No delegations yet. Chat the orchestrator or submit a task and
          the board fills in.
        </div>
      ) : view === "kanban" ? (
        <div data-testid="plan-kanban" className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-3">
          {board.columns.map((col) => (
            <section
              key={col.status}
              data-testid={`plan-col-${col.status}`}
              aria-label={`${col.status} (${col.items.length})`}
              className="rounded-lg border border-border p-2 space-y-2 min-h-24"
            >
              <h2 className="flex items-center justify-between px-1">
                <StatusPill status={col.status} />
                <span className="text-xs text-muted-foreground">{col.items.length}</span>
              </h2>
              {col.items.map((r) => (
                <PlanCard key={r.delegation_id} record={r} onOpen={setOpenId} />
              ))}
            </section>
          ))}
        </div>
      ) : (
        <div data-testid="plan-table" className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Agent</th>
                <th className="px-3 py-2 font-medium">Task</th>
                <th className="px-3 py-2 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {PLAN_COLUMNS.flatMap((s) =>
                board.columns
                  .find((c) => c.status === s)!
                  .items.map((r) => (
                    <tr
                      key={r.delegation_id}
                      data-testid={`plan-row-${r.delegation_id}`}
                      onClick={() => setOpenId(r.delegation_id)}
                      className="border-b border-border last:border-0 hover:bg-muted/50 cursor-pointer"
                    >
                      <td className="px-3 py-2 whitespace-nowrap">
                        <StatusPill status={r.status} />
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <KindPill kind={r.kind} />
                        <span className="text-xs">{r.agent}</span>
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {truncate(r.task, 90)}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">
                        {formatRelativeTime(r.updated_at)}
                      </td>
                    </tr>
                  )),
              )}
            </tbody>
          </table>
        </div>
      )}

      {openId && (
        <DetailView delegationId={openId} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}

function PlanCard({
  record: r,
  onOpen,
}: {
  record: Delegation;
  onOpen: (id: string) => void;
}) {
  return (
    <button
      type="button"
      data-testid={`plan-card-${r.delegation_id}`}
      onClick={() => onOpen(r.delegation_id)}
      className="w-full text-left rounded-md border border-border p-2 space-y-1 hover:bg-muted/50"
    >
      <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
        <KindPill kind={r.kind} />
        <span className="truncate">{r.agent}</span>
        <span className="ml-auto shrink-0">{formatRelativeTime(r.updated_at)}</span>
      </div>
      <p className="text-xs leading-snug line-clamp-3">{truncate(r.task, 140)}</p>
      {r.status === "failed" && r.error && (
        <p className="text-[11px] text-amber-700 dark:text-amber-300">
          {truncate(r.error, 100)}
        </p>
      )}
    </button>
  );
}

function PlanRow({
  record: r,
  onOpen,
}: {
  record: Delegation;
  onOpen: (id: string) => void;
}) {
  return (
    <li>
      <button
        type="button"
        data-testid={`plan-bug-${r.delegation_id}`}
        onClick={() => onOpen(r.delegation_id)}
        className="w-full flex items-center gap-2 text-left rounded-md px-2 py-1.5 hover:bg-muted/50"
      >
        <StatusPill status={r.status} />
        <KindPill kind={r.kind} />
        <span className="text-xs truncate flex-1">{truncate(r.task, 110)}</span>
        {r.error && (
          <span className="text-[11px] text-amber-700 dark:text-amber-300 truncate max-w-64">
            {truncate(r.error, 80)}
          </span>
        )}
      </button>
    </li>
  );
}
