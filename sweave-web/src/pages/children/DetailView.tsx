/**
 * Delegation detail view (M1.9 Step 3).
 *
 * Renders the data from ``GET /api/delegations/{id}/detail``:
 *   * Composed prompt (the M1.7 step 4 audit payload)
 *   * Tool timeline (callID-keyed, snapshot state per tool)
 *   * Tokens / cost (per-turn tokens_used aggregator)
 *   * Status timeline (queued -> running -> review | done | failed)
 *
 * The view is the UI surface for the M1.9 detail view (the CLI
 * log command + the REST endpoint + this modal all read the same
 * trace JSONL via ``sweave/web/detail_view.py`` on the server).
 *
 * The modal closes on Esc / backdrop click; the parent passes an
 * `onClose` callback.
 */
import { useQuery } from "@tanstack/react-query";
import { X, ChevronDown, ChevronRight } from "lucide-react";
import { useState, useEffect } from "react";
import { api } from "@/api/client";
import { cn } from "@/utils/cn";
import type { DelegationDetail, ToolTimelineEntry } from "@/types";

export interface DetailViewProps {
  delegationId: string;
  onClose: () => void;
}

export function DetailView({ delegationId, onClose }: DetailViewProps) {
  const { data, isLoading, error } = useQuery<DelegationDetail>({
    queryKey: ["delegation-detail", delegationId],
    queryFn: () => api.getDelegationDetail(delegationId),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      data-testid="detail-modal"
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-card border border-border rounded shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <header className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="text-sm font-semibold">
            Delegation {delegationId.slice(0, 8)}…
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            data-testid="detail-close"
            className="p-1 rounded hover:bg-muted"
          >
            <X size={16} />
          </button>
        </header>
        <div className="overflow-y-auto p-4 space-y-4">
          {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {error && (
            <p className="text-sm text-rose-500">
              Failed to load detail: {(error as Error).message}
            </p>
          )}
          {data && (
            <>
              <ComposedPromptSection
                data-testid="composed-prompt"
                composed={data.composed_prompt}
              />
              <ToolTimelineSection
                data-testid="tool-timeline"
                tools={data.tool_timeline}
              />
              <TokensSection data-testid="tokens" tokens={data.tokens} />
              <StatusTimelineSection
                data-testid="status-timeline"
                timeline={data.status_timeline}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function ComposedPromptSection({
  composed,
}: {
  composed: DelegationDetail["composed_prompt"];
  "data-testid"?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!composed) {
    return (
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          Composed prompt
        </h3>
        <p className="text-xs text-muted-foreground">No data.</p>
      </section>
    );
  }
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Composed prompt
      </button>
      <dl className="grid grid-cols-2 gap-2 text-xs">
        {(
          [
            ["memory", composed.memory_chars],
            ["whats_new", composed.whats_new_chars],
            ["synthesis", composed.synthesis_chars],
            ["transcript_ref", composed.transcript_ref_chars],
            ["user", composed.user_chars],
            ["dropped", composed.dropped_memory + composed.dropped_whats_new + composed.dropped_synthesis],
          ] as const
        ).map(([k, v]) => (
          <div
            key={k}
            className="flex items-center justify-between border border-border rounded px-2 py-1"
          >
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="font-mono">{v}</dd>
          </div>
        ))}
      </dl>
      {open && (
        <pre className="mt-2 text-[10px] font-mono whitespace-pre-wrap break-words p-2 bg-muted rounded border border-border max-h-48 overflow-auto">
          {JSON.stringify(composed, null, 2)}
        </pre>
      )}
    </section>
  );
}

function ToolTimelineSection({
  tools,
}: {
  tools: ToolTimelineEntry[];
  "data-testid"?: string;
}) {
  if (tools.length === 0) {
    return (
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          Tool timeline
        </h3>
        <p className="text-xs text-muted-foreground">
          No tool calls in this delegation.
        </p>
      </section>
    );
  }
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
        Tool timeline
      </h3>
      <ul className="space-y-1">
        {tools.map((t) => (
          <li
            key={t.callID}
            data-testid={`tool-${t.callID}`}
            className="border border-border rounded p-2"
          >
            <div className="flex items-center gap-2 text-xs">
              <span className="font-mono text-muted-foreground">
                {t.callID}
              </span>
              {t.tool && (
                <span className="px-1.5 py-0.5 bg-muted rounded">{t.tool}</span>
              )}
              {t.status && (
                <span
                  className={cn(
                    "px-1.5 py-0.5 rounded",
                    t.status === "completed" && "bg-emerald-500/15 text-emerald-700",
                    t.status === "error" && "bg-rose-500/15 text-rose-700",
                    t.status === "running" && "bg-amber-500/15 text-amber-700",
                  )}
                >
                  {t.status}
                </span>
              )}
            </div>
            {typeof t.output !== "undefined" && t.output !== null && (
              <pre className="mt-1 text-[10px] font-mono whitespace-pre-wrap break-words p-1 bg-muted rounded max-h-32 overflow-auto">
                {String(typeof t.output === "string" ? t.output : JSON.stringify(t.output, null, 2))}
              </pre>
            )}
            {t.error && (
              <p className="mt-1 text-[10px] text-rose-600 font-mono">{t.error}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function TokensSection({
  tokens,
}: {
  tokens: DelegationDetail["tokens"];
  "data-testid"?: string;
}) {
  if (!tokens) return null;
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
        Tokens / cost
      </h3>
      <dl className="grid grid-cols-3 gap-2 text-xs">
        {(
          [
            ["input", tokens.input],
            ["output", tokens.output],
            ["reasoning", tokens.reasoning],
            ["cache_read", tokens.cache_read],
            ["cache_write", tokens.cache_write],
            ["cost", tokens.cost],
          ] as const
        ).map(([k, v]) => (
          <div
            key={k}
            className="flex items-center justify-between border border-border rounded px-2 py-1"
          >
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="font-mono">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function StatusTimelineSection({
  timeline,
}: {
  timeline: DelegationDetail["status_timeline"];
  "data-testid"?: string;
}) {
  if (timeline.length === 0) {
    return (
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          Status timeline
        </h3>
        <p className="text-xs text-muted-foreground">No status changes.</p>
      </section>
    );
  }
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
        Status timeline
      </h3>
      <ul className="space-y-0.5">
        {timeline.map((c, i) => (
          <li
            key={`${c.status}-${i}`}
            className="flex items-center gap-2 text-xs"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground" />
            <span className="font-mono">{c.status}</span>
            {c.source && (
              <span className="text-muted-foreground">({c.source})</span>
            )}
            {c.ts && (
              <span className="text-muted-foreground ml-auto">{c.ts}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
