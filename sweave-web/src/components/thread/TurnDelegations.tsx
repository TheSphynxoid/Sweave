/**
 * Inline delegation cards for a chat turn (chat-transparency slice).
 *
 * Every assistant message carries its chat-turn delegation id in
 * `metadata.custom.delegationId` (projected by `lib/chat/runtime.ts`).
 * When the orchestrator deferred work during that turn, the child
 * delegations (`parent_task_id == turn id`) render here as compact
 * cards — agent + status pill + task snippet — so the turn never
 * reads as "waiting blindly". Cards expand to an output summary and
 * open the full M1.9 `DetailView` modal (the same component the
 * Children tab uses).
 *
 * Live + historical: the component is mounted under EVERY assistant
 * message with a delegation id. The in-flight turn's cards pulse via
 * `delegation.status_changed` WS resubscription; settled turns
 * re-render from the same fetch. Renders null when the turn has no
 * children, so childless turns are byte-identical to before.
 *
 * Deliberately provider-independent (plain fetch + guarded `useWS`,
 * no React Query): the dev lab renders the real `Thread` with
 * neither provider, and fixtures carry no delegation ids.
 */

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import { DetailView } from "@/pages/children/DetailView";
import { cn } from "@/utils/cn";
import type { Delegation, DelegationStatus } from "@/types";

// Status pill twins LiveTree's STATUS_CLASS/STATUS_LABEL
// (`pages/children/LiveTree.tsx` is canonical; keep in sync).
const STATUS_CLASS: Record<DelegationStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  review: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
};

const STATUS_LABEL: Record<DelegationStatus, string> = {
  queued: "Queued",
  running: "Running",
  review: "Review",
  done: "Done",
  failed: "Failed",
};

const TASK_SNIPPET_CHARS = 140;
const OUTPUT_SNIPPET_CHARS = 600;

function truncate(text: string, max: number): string {
  const clean = text.trim().replace(/\s+/g, " ");
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

export function TurnDelegations({ parentDelegationId }: { parentDelegationId: string }) {
  const [children, setChildren] = useState<Delegation[] | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await api.listDelegations({ parent_task_id: parentDelegationId });
      setChildren(rows);
    } catch {
      // A failed children fetch must never break the thread; the
      // turn's text stands on its own. Null = not yet loaded AND
      // failed — both render nothing (see below).
    }
  }, [parentDelegationId]);

  useEffect(() => {
    setChildren(null);
    setExpandedId(null);
    let cancelled = false;
    void (async () => {
      try {
        const rows = await api.listDelegations({ parent_task_id: parentDelegationId });
        if (!cancelled) setChildren(rows);
      } catch {
        // Children are supplementary, never load-bearing (see load()).
      }
    })();
    return () => {
      cancelled = true;
    };
    // `load` is the same fetch without the mount guards; calling it
    // here would double-fetch. eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parentDelegationId]);

  // Live pulse: any delegation status change may concern this turn's
  // children (child events carry no parent link, so resubscribe the
  // whole list — it is tiny and parent-scoped). The dev lab renders
  // without a WSProvider; default to unsubscribed there.
  let subscribe: ((event: string, handler: () => void) => () => void) | null = null;
  try {
    subscribe = useWS().subscribe;
  } catch {
    subscribe = null;
  }
  useEffect(() => {
    if (!subscribe) return;
    return subscribe("delegation.status_changed", () => {
      void load();
    });
  }, [subscribe, load]);

  if (!children || children.length === 0) return null;
  const ordered = [...children].sort((a, b) =>
    a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0,
  );

  return (
    <div className="mt-2 space-y-1.5" data-testid="turn-delegations">
      {ordered.map((child) => {
        const expanded = expandedId === child.delegation_id;
        const summary = child.status === "failed" ? child.error || "" : child.output || "";
        return (
          <div
            key={child.delegation_id}
            data-testid="turn-delegation-card"
            data-delegation-id={child.delegation_id}
            className="rounded-lg border border-border bg-card/60"
          >
            <button
              type="button"
              onClick={() => setExpandedId(expanded ? null : child.delegation_id)}
              aria-expanded={expanded}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs"
            >
              {expanded ? (
                <ChevronDown size={13} className="shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight size={13} className="shrink-0 text-muted-foreground" />
              )}
              <span className="font-medium text-foreground">{child.agent}</span>
              <span
                data-testid={`status-pill-${child.status}`}
                className={cn(
                  "rounded px-1.5 py-px text-[10px] font-medium",
                  STATUS_CLASS[child.status],
                )}
              >
                {STATUS_LABEL[child.status]}
              </span>
              {child.needs_attention && (
                <span className="text-[10px] font-medium text-amber-600 dark:text-amber-400">
                  • needs input
                </span>
              )}
              <span className="min-w-0 flex-1 truncate text-muted-foreground">
                {truncate(child.task, TASK_SNIPPET_CHARS)}
              </span>
            </button>
            {expanded && (
              <div className="space-y-2 border-t border-border px-2.5 py-2">
                {child.needs_attention && (
                  <ChildEscalationPreview delegationId={child.delegation_id} />
                )}
                {summary ? (
                  <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-muted-foreground">
                    {truncate(summary, OUTPUT_SNIPPET_CHARS)}
                  </p>
                ) : (
                  <p className="text-xs italic text-muted-foreground/70">
                    No output yet — the specialist is still working.
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => setDetailId(child.delegation_id)}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
                >
                  <ExternalLink size={11} />
                  Open full detail
                </button>
              </div>
            )}
          </div>
        );
      })}
      {detailId && <DetailView delegationId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}

/** Escalation preview inside an expanded child card (M1.11 audit). */
function ChildEscalationPreview({ delegationId }: { delegationId: string }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rec = await api.getEscalation(delegationId);
        if (!cancelled) {
          setText(
            rec
              ? `${rec.kind === "escalation" ? "Escalation" : "Question"} (${rec.status}): ${rec.question}`
              : null,
          );
        }
      } catch {
        if (!cancelled) setText(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [delegationId]);
  if (!text) return null;
  return (
    <p
      data-testid="turn-delegation-escalation"
      className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-xs leading-relaxed text-amber-700 dark:text-amber-300"
    >
      {text.length > 280 ? `${text.slice(0, 280)}…` : text} — answer in the
      Children audit log.
    </p>
  );
}
