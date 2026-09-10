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
import { BashTool } from "@/components/agent-elements/tools/bash-tool";
import { EditTool } from "@/components/agent-elements/tools/edit-tool";
import { AgentToolCard } from "@/components/agent/AgentToolCard";
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
              <EscalationSection delegationId={delegationId} />
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

function EscalationSection({ delegationId }: { delegationId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["escalation", delegationId],
    queryFn: () => api.getEscalation(delegationId),
  });
  if (isLoading) {
    return (
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          Escalation
        </h3>
        <p className="text-xs text-muted-foreground">Loading…</p>
      </section>
    );
  }
  if (!data) return null;
  return (
    <section data-testid="escalation-section">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
        Escalation — {data.kind} → {data.audience} · {data.status}
      </h3>
      <p className="text-xs whitespace-pre-wrap break-words">{data.question}</p>
      {data.options && data.options.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          Options: {data.options.join(" · ")}
        </p>
      )}
      <p className="mt-1 text-xs text-muted-foreground">
        {data.status === "pending"
          ? "Waiting — no deadline. Answer or skip below; the asking turn holds."
          : `Response: ${data.response ?? "(none)"}`}
      </p>
    </section>
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
      <ul className="space-y-2">
        {tools.map((t) => (
          <li
            key={t.callID}
            data-testid={`tool-${t.callID}`}
            className="animate-in fade-in-0 slide-in-from-bottom-1"
          >
            {isBashTool(t) ? (
              <BashTool part={toBashPart(t)} />
            ) : isEditTool(t) ? (
              <EditTool part={toEditPart(t)} isCollapsible />
            ) : (
              <AgentToolCard
                tool={t.tool ?? "tool"}
                status={t.status ?? "unknown"}
                input={t.input}
                output={t.output}
                error={t.error}
              />
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

/** Map a backend tool timeline entry to the AI-SDK-style `part` shape the
 *  agent-elements BashTool card expects. */
function toBashPart(t: ToolTimelineEntry): Record<string, unknown> {
  const state =
    t.status === "completed"
      ? "output-available"
      : t.status === "running"
        ? "input-streaming"
        : "call";
  const input =
    typeof t.input === "object" && t.input
      ? (t.input as Record<string, unknown>)
      : { command: typeof t.input === "string" ? t.input : t.title ?? t.tool ?? "" };
  return {
    id: t.callID,
    toolCallId: t.callID,
    toolName: t.tool ?? "Bash",
    state,
    input,
    output: t.output,
    result: t.error ? { error: t.error } : t.output,
  };
}

function isBashTool(t: ToolTimelineEntry): boolean {
  return /bash|shell|terminal|sh$/i.test(t.tool ?? "") || (t.title ?? "").includes("$");
}

function isEditTool(t: ToolTimelineEntry): boolean {
  const tool = t.tool ?? "";
  if (!/edit|write|create|file|patch/i.test(tool)) return false;
  const input = typeof t.input === "object" && t.input ? (t.input as Record<string, unknown>) : {};
  return "file_path" in input || "old_string" in input || "new_string" in input || "path" in input;
}

function toEditPart(t: ToolTimelineEntry): Record<string, unknown> {
  const state =
    t.status === "completed"
      ? "output-available"
      : t.status === "running"
        ? "input-streaming"
        : "call";
  const input =
    typeof t.input === "object" && t.input
      ? (t.input as Record<string, unknown>)
      : {};
  const isWrite = /write|create/i.test(t.tool ?? "");
  return {
    id: t.callID,
    toolCallId: t.callID,
    type: isWrite ? "tool-write" : "tool-edit",
    state,
    input,
    output: t.output,
    result: t.error ? { error: t.error } : t.output,
  };
}
