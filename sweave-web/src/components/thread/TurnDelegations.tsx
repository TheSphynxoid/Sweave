/**
 * Inline delegation cards for a chat turn (chat-transparency slice).
 *
 * Every assistant message carries its chat-turn delegation id in
 * `metadata.custom.delegationId` (projected by `lib/chat/runtime.ts`).
 * When the orchestrator deferred work during that turn, the child
 * delegations (`parent_task_id == turn id`) render here as a compact
 * activity timeline — agent + status pill + task snippet — so the turn
 * never reads as "waiting blindly". Cards expand to an output summary
 * and open the full M1.9 `DetailView` modal (the same component the
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
import { ChevronDown, ExternalLink, Network } from "lucide-react";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import { DetailView } from "@/pages/children/DetailView";
import { StatusPill } from "@/components/delegation/StatusPill";
import { isTimeoutDelegation, parseTurnTimeout, formatRuntime } from "@/lib/delegation/taxonomy";
import type { Delegation, EscalationRecord } from "@/types";
import { cn } from "@/utils/cn";

const TASK_SNIPPET_CHARS = 140;
const OUTPUT_SNIPPET_CHARS = 600;

function truncate(text: string, max: number): string {
  const clean = text.trim().replace(/\s+/g, " ");
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

function agentInitial(agent: string): string {
  const clean = agent.trim();
  return clean ? clean.charAt(0).toUpperCase() : "?";
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
    // Live pulse: status changes move pills; escalation events light
    // the needs-attention badge + inline answer card up (a permission
    // ask can arrive minutes AFTER the parent turn settled — the
    // M1.12 stuck-reviewer incident — so the escalation events must
    // refetch independently of status changes).
    const offs = [
      subscribe("delegation.status_changed", () => {
        void load();
      }),
      subscribe("specialist.escalated", () => {
        void load();
      }),
      subscribe("specialist.escalation_resolved", () => {
        void load();
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [subscribe, load]);

  if (!children || children.length === 0) return null;
  const ordered = [...children].sort((a, b) =>
    a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0,
  );
  const activeCount = ordered.filter(
    (c) => c.status === "running" || c.status === "queued",
  ).length;

  return (
    <div
      className="animate-fade-in mt-3 overflow-hidden rounded-xl border border-border/60 bg-muted/20"
      data-testid="turn-delegations"
    >
      <div
        className="flex items-center gap-2 border-b border-border/50 bg-muted/40 px-3 py-1.5"
        data-testid="turn-delegations-header"
      >
        <Network size={12} className="text-primary" />
        <span className="text-[11px] font-semibold text-foreground">
          Specialist activity
        </span>
        <span className="text-[11px] tabular-nums text-muted-foreground">
          {ordered.length} task{ordered.length === 1 ? "" : "s"}
        </span>
        {activeCount > 0 && (
          <span className="flex items-center gap-1 rounded-full bg-primary/10 px-2 py-px text-[10px] font-medium text-primary">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
            {activeCount} running
          </span>
        )}
      </div>
      <ol className="relative space-y-1 px-2 py-2 before:absolute before:bottom-3 before:left-[21px] before:top-3 before:w-px before:bg-border/60">
        {ordered.map((child) => {
          const expanded = expandedId === child.delegation_id;
          const timedOut = isTimeoutDelegation(child);
          const summary = child.status === "failed" && !timedOut ? child.error || "" : child.output || "";
          const runtime =
            child.status === "done"
              ? formatRuntime(child.started_at, child.completed_at)
              : null;
          const live = child.status === "running" || child.status === "queued";
          return (
            <li key={child.delegation_id} className="relative flex gap-2">
              <span
                aria-hidden
                className={cn(
                  "z-10 mt-2.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[9px] font-bold",
                  live
                    ? "border-primary/50 bg-primary/15 text-primary"
                    : child.status === "done"
                      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
                      : child.status === "failed"
                        ? "border-rose-500/40 bg-rose-500/10 text-rose-600 dark:text-rose-300"
                        : "border-border bg-card text-muted-foreground",
                )}
              >
                {live ? (
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                ) : (
                  agentInitial(child.agent)
                )}
              </span>
              <div
                data-testid="turn-delegation-card"
                data-delegation-id={child.delegation_id}
                className={cn(
                  "min-w-0 flex-1 rounded-lg border bg-card/80 backdrop-blur transition-all",
                  expanded
                    ? "border-primary/40 shadow-md shadow-primary/5"
                    : "border-border/60 hover:border-primary/30 hover:shadow-sm",
                )}
              >
                <button
                  type="button"
                  onClick={() => setExpandedId(expanded ? null : child.delegation_id)}
                  aria-expanded={expanded}
                  className="flex w-full items-center gap-2 px-2.5 py-2 text-left text-xs"
                >
                  <ChevronDown
                    size={13}
                    className={cn(
                      "shrink-0 text-muted-foreground transition-transform",
                      expanded ? "" : "-rotate-90",
                    )}
                  />
                  <span className="shrink-0 font-semibold text-foreground">{child.agent}</span>
                  <StatusPill
                    status={child.status}
                    error={child.error}
                    startedAt={child.started_at}
                    completedAt={child.completed_at}
                  />
                  {child.needs_attention && (
                    <span className="flex shrink-0 items-center gap-1 rounded-full bg-amber-500/15 px-1.5 py-px text-[10px] font-semibold text-amber-700 dark:text-amber-300">
                      <span className="h-1 w-1 animate-pulse rounded-full bg-current" />
                      needs input
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {truncate(child.task, TASK_SNIPPET_CHARS)}
                  </span>
                </button>
                {expanded && (
                  <div className="animate-fade-in space-y-2 border-t border-border/50 px-2.5 py-2">
                    {child.needs_attention && (
                      <ChildEscalationPreview delegationId={child.delegation_id} />
                    )}
                    {timedOut && (
                      <TimeoutNotice
                        error={child.error}
                        hasOutput={!!child.output.trim()}
                      />
                    )}
                    {timedOut ? (
                      child.output.trim() ? (
                        <p className="whitespace-pre-wrap break-words rounded-lg bg-muted/50 px-2.5 py-2 text-xs leading-relaxed text-muted-foreground">
                          {truncate(child.output, OUTPUT_SNIPPET_CHARS)}
                        </p>
                      ) : null
                    ) : summary ? (
                      <p className="whitespace-pre-wrap break-words rounded-lg bg-muted/50 px-2.5 py-2 text-xs leading-relaxed text-muted-foreground">
                        {truncate(summary, OUTPUT_SNIPPET_CHARS)}
                      </p>
                    ) : (
                      <p className="flex items-center gap-1.5 px-1 text-xs italic text-muted-foreground/70">
                        <span className="typing-dots" aria-hidden>
                          <span />
                          <span />
                          <span />
                        </span>
                        The specialist is still working.
                      </p>
                    )}
                    {runtime && (
                      <p
                        data-testid="turn-delegation-runtime"
                        className="px-1 text-[10px] tabular-nums text-muted-foreground"
                      >
                        Ran for {runtime}.
                      </p>
                    )}
                    <button
                      type="button"
                      onClick={() => setDetailId(child.delegation_id)}
                      className="inline-flex items-center gap-1 px-1 text-[11px] font-semibold text-primary hover:underline"
                    >
                      <ExternalLink size={11} />
                      Open full detail
                    </button>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {detailId && <DetailView delegationId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}

/**
 * Explanatory banner for a timed-out child card. Per the timeout
 * ruling: calm amber copy, never the red-failure treatment. Shows
 * the parsed budget and the "state at timeout" hint (partial output
 * present / none persisted). The raw sentinel stays as a mono
 * audit line.
 */
function TimeoutNotice({ error, hasOutput }: { error: string | null; hasOutput: boolean }) {
  const timeout = parseTurnTimeout(error);
  return (
    <div
      data-testid="turn-delegation-timeout"
      className="rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-2.5 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-300"
    >
      <p>
        The specialist ran out of its turn budget
        {timeout?.seconds ? ` (${Math.round(timeout.seconds / 60)} min limit)` : ""}; partial
        output may still be present.
      </p>
      {!hasOutput && (
        <p className="text-[11px] text-amber-700/80 dark:text-amber-300/80">
          No output was persisted before the timeout.
        </p>
      )}
      {error && (
        <code
          data-testid="turn-delegation-timeout-raw"
          className="mt-1 block font-mono text-[10px] text-muted-foreground"
        >
          {error}
        </code>
      )}
    </div>
  );
}

/** Escalation preview inside an expanded child card (M1.11 audit).
 *
 * M1.12 fix: pending questions render their options INLINE (the
 * "ask card") instead of bouncing the user to the Children audit
 * log — the question can outlive the turn's own UI surface, and
 * the asking turn holds until answered, so the card must answer.
 */
function ChildEscalationPreview({ delegationId }: { delegationId: string }) {
  const [rec, setRec] = useState<EscalationRecord | null | "error">(null);
  const [busy, setBusy] = useState(false);

  const loadRec = useCallback(async () => {
    try {
      const fetched = await api.getEscalation(delegationId);
      setRec(fetched);
    } catch {
      setRec("error");
    }
  }, [delegationId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const fetched = await api.getEscalation(delegationId);
        if (!cancelled) setRec(fetched);
      } catch {
        if (!cancelled) setRec("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [delegationId]);

  const answer = async (response: string) => {
    setBusy(true);
    try {
      await api.answerEscalation(delegationId, response);
      await loadRec();
    } catch {
      // leave the card as-is; the audit log remains the fallback
    } finally {
      setBusy(false);
    }
  };

  const skip = async () => {
    // Same system-confirm guard the M1.11 Question card uses.
    if (!window.confirm("Skip this question? Skip = deny.")) return;
    setBusy(true);
    try {
      await api.skipEscalation(delegationId);
      await loadRec();
    } catch {
      // ignore; card stays
    } finally {
      setBusy(false);
    }
  };

  if (rec === "error") return null;
  if (!rec) return null;
  const label = rec.kind === "escalation" ? "Escalation" : "Question";
  return (
    <div
      data-testid="turn-delegation-escalation"
      className="space-y-1.5 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-2.5 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-300"
    >
      <p>
        {label} ({rec.status}):{" "}
        {rec.question.length > 280 ? `${rec.question.slice(0, 280)}…` : rec.question}
      </p>
      {rec.status === "pending" && rec.options && (
        <div className="flex flex-wrap items-center gap-1.5">
          {rec.options.map((opt) => (
            <button
              key={opt}
              type="button"
              disabled={busy}
              onClick={() => void answer(opt)}
              data-testid={`turn-escalation-option`}
              className="rounded-full border border-amber-500/40 bg-background px-2.5 py-1 text-[11px] font-semibold text-amber-700 transition-all hover:-translate-y-px hover:bg-amber-500/10 hover:shadow-sm disabled:opacity-50 dark:text-amber-300"
            >
              {opt}
            </button>
          ))}
          <button
            type="button"
            disabled={busy}
            onClick={() => void skip()}
            data-testid="turn-escalation-skip"
            className="text-[11px] text-muted-foreground underline hover:text-foreground disabled:opacity-50"
          >
            skip = deny
          </button>
        </div>
      )}
    </div>
  );
}
