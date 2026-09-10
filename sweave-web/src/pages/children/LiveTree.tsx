/**
 * Live tree (M1.9 Step 3).
 *
 * The Children page's primary view. Renders a tree of
 * delegations with:
 *   * status pills (queued / running / review / done / failed)
 *   * depth = tree indent (parent_task_id)
 *   * promote button on every ``review`` record
 *   * answer input on every ``needs_attention`` record
 *   * click row -> open the detail modal
 *
 * WS events update the list: ``delegation.status_changed`` pulses
 * the row's status + invalidates the React Query cache. The
 * tree rebuild is O(n) per change; the row-level patch keeps
 * the change local (the M1.8 no-rerender invariant + the plan's
 * "Children-tab live updates" -- same single-node patch pattern).
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ChevronDown, Send, X, AlertCircle } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { buildDelegationTree, findEscalatingNodes, type TreeNode } from "./tree";
import { cn } from "@/utils/cn";
import type { Delegation, DelegationStatus } from "@/types";

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

export interface LiveTreeProps {
  delegations: Delegation[];
  onOpen: (delegationId: string) => void;
}

export function LiveTree({ delegations, onOpen }: LiveTreeProps) {
  const tree = useMemo(() => buildDelegationTree(delegations), [delegations]);
  const escalations = useMemo(() => findEscalatingNodes(tree), [tree]);

  if (delegations.length === 0) {
    return (
      <div
        data-testid="live-tree-empty"
        className="text-sm text-muted-foreground p-4 border border-dashed border-border rounded"
      >
        No delegations yet. Submit a task or chat the orchestrator to
        see the live tree.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {escalations.length > 0 && (
        <EscalationLane nodes={escalations} onOpen={onOpen} />
      )}
      <ul data-testid="live-tree" className="space-y-1">
        {tree.map((node) => (
          <TreeRow
            key={node.record.delegation_id}
            node={node}
            onOpen={onOpen}
          />
        ))}
      </ul>
    </div>
  );
}

function TreeRow({ node, onOpen }: { node: TreeNode; onOpen: (id: string) => void }) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = node.children.length > 0;
  const id = node.record.delegation_id;
  return (
    <li data-testid={`tree-row-${id}`} data-depth={node.depth}>
      <div
        className={cn(
          "flex items-center gap-2 px-2 py-1.5 rounded text-sm hover:bg-muted",
          node.record.needs_attention && "ring-1 ring-amber-500/40",
        )}
        style={{ paddingLeft: `${node.depth * 16 + 8}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            aria-label={expanded ? "Collapse" : "Expand"}
            data-testid={`tree-toggle-${id}`}
            className="p-0.5 text-muted-foreground hover:text-foreground"
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span className="w-4 inline-block" />
        )}
        <button
          type="button"
          onClick={() => onOpen(id)}
          data-testid={`tree-row-open-${id}`}
          className="flex-1 min-w-0 text-left truncate"
        >
          <span className="font-mono text-xs text-muted-foreground mr-2">
            {id.slice(0, 8)}…
          </span>
          <span className="truncate">{node.record.task || "(empty)"}</span>
        </button>
        <StatusPill status={node.record.status} />
        {node.record.status === "review" && (
          <PromoteButton delegationId={id} />
        )}
        {node.record.needs_attention && (
          <AnswerInline delegationId={id} />
        )}
      </div>
      {hasChildren && expanded && (
        <ul className="space-y-1">
          {node.children.map((c) => (
            <TreeRow key={c.record.delegation_id} node={c} onOpen={onOpen} />
          ))}
        </ul>
      )}
    </li>
  );
}

function StatusPill({ status }: { status: DelegationStatus }) {
  return (
    <span
      data-testid={`status-pill-${status}`}
      className={cn(
        "px-2 py-0.5 rounded text-[10px] uppercase tracking-wide",
        STATUS_CLASS[status],
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

function PromoteButton({ delegationId }: { delegationId: string }) {
  const qc = useQueryClient();
  const { pushNotification } = useApp();
  const m = useMutation({
    mutationFn: () => api.promoteDelegation(delegationId),
    onSuccess: () => {
      pushNotification("success", "Promoted to done");
      void qc.invalidateQueries({ queryKey: ["delegations"] });
    },
    onError: (err) => {
      pushNotification("error", `Promote failed: ${(err as Error).message}`);
    },
  });
  return (
    <button
      type="button"
      onClick={() => m.mutate()}
      disabled={m.isPending}
      data-testid={`promote-${delegationId}`}
      className="px-2 py-0.5 text-[10px] rounded bg-emerald-500 text-white disabled:opacity-50"
    >
      {m.isPending ? "Promoting…" : "Mark done"}
    </button>
  );
}

function AnswerInline({ delegationId }: { delegationId: string }) {
  const [value, setValue] = useState("");
  const [showInput, setShowInput] = useState(false);
  const qc = useQueryClient();
  const { pushNotification } = useApp();
  const m = useMutation({
    mutationFn: (response: string) => api.answerEscalation(delegationId, response),
    onSuccess: () => {
      pushNotification("success", "Answer sent");
      setValue("");
      setShowInput(false);
      void qc.invalidateQueries({ queryKey: ["delegations"] });
      void qc.invalidateQueries({ queryKey: ["escalation", delegationId] });
    },
    onError: (err) => {
      pushNotification("error", `Answer failed: ${(err as Error).message}`);
    },
  });
  const skip = useMutation({
    mutationFn: () => api.skipEscalation(delegationId),
    onSuccess: () => {
      pushNotification("success", "Question skipped — agent proceeds with best judgment");
      void qc.invalidateQueries({ queryKey: ["delegations"] });
      void qc.invalidateQueries({ queryKey: ["escalation", delegationId] });
    },
    onError: (err) => {
      pushNotification("error", `Skip failed: ${(err as Error).message}`);
    },
  });
  const sendSkip = () => {
    // System-issued confirm (not LLM text): the fat-finger guard.
    if (
      !window.confirm(
        "Skip this question? The agent will proceed with its best judgment. This cannot be undone.",
      )
    )
      return;
    skip.mutate();
  };
  if (!showInput) {
    return (
      <button
        type="button"
        onClick={() => setShowInput(true)}
        data-testid={`answer-toggle-${delegationId}`}
        className="px-2 py-0.5 text-[10px] rounded bg-amber-500 text-white flex items-center gap-1"
      >
        <AlertCircle size={10} /> Answer
      </button>
    );
  }
  return (
    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && value.trim()) m.mutate(value.trim());
          if (e.key === "Escape") setShowInput(false);
        }}
        disabled={m.isPending}
        data-testid={`answer-input-${delegationId}`}
        placeholder="Reply…"
        className="px-2 py-0.5 text-xs border border-border rounded bg-input w-40"
      />
      <button
        type="button"
        onClick={() => value.trim() && m.mutate(value.trim())}
        disabled={m.isPending || !value.trim()}
        data-testid={`answer-send-${delegationId}`}
        className="p-1 text-emerald-600 disabled:opacity-50"
        aria-label="Send answer"
      >
        <Send size={12} />
      </button>
      <button
        type="button"
        onClick={sendSkip}
        disabled={skip.isPending}
        data-testid={`skip-${delegationId}`}
        title="Skip — agent proceeds with best judgment (asks first)"
        className="px-1.5 py-0.5 text-[10px] rounded border border-border text-muted-foreground hover:text-foreground disabled:opacity-50"
      >
        Skip
      </button>
      <button
        type="button"
        onClick={() => setShowInput(false)}
        aria-label="Cancel"
        className="p-1 text-muted-foreground"
      >
        <X size={12} />
      </button>
    </div>
  );
}

function EscalationLane({
  nodes,
  onOpen,
}: {
  nodes: TreeNode[];
  onOpen: (id: string) => void;
}) {
  return (
    <div
      data-testid="escalation-lane"
      className="border border-amber-500/40 rounded bg-amber-500/5 p-3"
    >
      <h3 className="text-xs uppercase tracking-wide text-amber-700 dark:text-amber-300 font-semibold mb-2 flex items-center gap-2">
        <AlertCircle size={14} />
        Needs attention ({nodes.length})
      </h3>
      <ul className="space-y-1">
        {nodes.map((n) => (
          <li
            key={n.record.delegation_id}
            data-testid={`escalation-row-${n.record.delegation_id}`}
            className="flex items-center gap-2 text-sm"
          >
            <button
              type="button"
              onClick={() => onOpen(n.record.delegation_id)}
              className="flex-1 min-w-0 text-left"
            >
              <span className="flex items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">
                  {n.record.delegation_id.slice(0, 8)}…
                </span>
                <KindBadge delegationId={n.record.delegation_id} />
              </span>
              <span className="block truncate text-muted-foreground">
                {n.record.task || "(empty)"}
              </span>
              <EscalationPreview delegationId={n.record.delegation_id} />
            </button>
            <AnswerInline delegationId={n.record.delegation_id} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function KindBadge({ delegationId }: { delegationId: string }) {
  const [kind, setKind] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rec = await api.getEscalation(delegationId);
        if (!cancelled) setKind(rec?.kind ?? null);
      } catch {
        if (!cancelled) setKind(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [delegationId]);
  if (!kind) return null;
  return (
    <span
      data-testid={`kind-badge-${kind}`}
      className="rounded bg-amber-500/20 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300"
    >
      {kind === "escalation" ? "ESC" : "Q"}
    </span>
  );
}

function EscalationPreview({ delegationId }: { delegationId: string }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rec = await api.getEscalation(delegationId);
        if (!cancelled) setText(rec ? rec.question : null);
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
    <span
      data-testid="escalation-preview"
      className="block truncate text-xs text-foreground/80"
    >
      {text.length > 120 ? `${text.slice(0, 120)}…` : text}
    </span>
  );
}
