/**
 * Delegation detail — tab content components (container-agnostic).
 *
 * These are the presentational building blocks of the tabbed
 * `DetailView` (rescoped Step 4a). Per ruling Q6 the dock-deferred
 * plan, they MUST NOT assume a modal: no `createPortal`, no
 * `document.body` access, no `Escape`-key listeners, no window
 * reads. They are plain functions of props + their own local
 * expand/collapse state so they can graduate into a docked pane
 * later (see docs/SPECIALIST_VIEW_PLAN.md §Q6). The modal chrome
 * (portal, overlay, close affordance, live poll wiring) lives in
 * `DetailView.tsx`.
 *
 * Tabs:
 *   Overview      — escalation + identity/state summary
 *   Transcript    — rescoped-2a blocks, chronological (degrades)
 *   Tools         — `tool_timeline`, newest-first (ruling Q2, UI-only)
 *   Prompt        — composed/audit sections
 *   Tokens/Status — existing sections, chronological
 */

import { useState } from "react";
import { Markdown } from "@/components/thread/markdown/Markdown";
import { BashTool } from "@/components/agent-elements/tools/bash-tool";
import { EditTool } from "@/components/agent-elements/tools/edit-tool";
import { AgentToolCard } from "@/components/agent/AgentToolCard";
import type {
  ComposedPrompt,
  DelegationDetail,
  EscalationRecord,
  ToolTimelineEntry,
  TranscriptBlock,
} from "@/types";

// ---------------------------------------------------------------------------
// Shared helpers (pure — no DOM, no hooks beyond local state)
// ---------------------------------------------------------------------------

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
  return (
    "file_path" in input ||
    "old_string" in input ||
    "new_string" in input ||
    "path" in input
  );
}

function toEditPart(t: ToolTimelineEntry): Record<string, unknown> {
  const state =
    t.status === "completed"
      ? "output-available"
      : t.status === "running"
        ? "input-streaming"
        : "call";
  const input =
    typeof t.input === "object" && t.input ? (t.input as Record<string, unknown>) : {};
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

/** Render a single tool timeline entry via the agent-elements cards. */
export function ToolTimelineRow({ tool }: { tool: ToolTimelineEntry }) {
  return (
    <li
      key={tool.callID}
      data-testid={`tool-${tool.callID}`}
      className="animate-in fade-in-0 slide-in-from-bottom-1"
    >
      {isBashTool(tool) ? (
        <BashTool part={toBashPart(tool)} />
      ) : isEditTool(tool) ? (
        <EditTool part={toEditPart(tool)} isCollapsible />
      ) : (
        <AgentToolCard
          tool={tool.tool ?? "tool"}
          status={tool.status ?? "unknown"}
          input={tool.input}
          output={tool.output}
          error={tool.error}
        />
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Overview tab
// ---------------------------------------------------------------------------

export function OverviewTab({
  delegationId,
  escalation,
  detail,
}: {
  delegationId: string;
  escalation: EscalationRecord | null | "error";
  detail: DelegationDetail;
}) {
  return (
    <div className="space-y-4">
      <EscalationTabContent delegationId={delegationId} escalation={escalation} />
      <IdentitySummary detail={detail} />
    </div>
  );
}

function EscalationTabContent({
  delegationId,
  escalation,
}: {
  delegationId: string;
  escalation: EscalationRecord | null | "error";
}) {
  // The modal-level DetailView owns the fetch (so the Overview tab
  // and the old inline section stay consistent); here we only render
  // what was handed down. We keep the same testid the old section
  // used so existing assertions keep working.
  void delegationId;
  if (escalation === "error" || escalation == null) return null;
  return (
    <section
      data-testid={delegationId ? "escalation-section" : "escalation-section"}
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
        Escalation — {escalation.kind} → {escalation.audience} · {escalation.status}
      </h3>
      <p className="text-xs whitespace-pre-wrap break-words">{escalation.question}</p>
      {escalation.options && escalation.options.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          Options: {escalation.options.join(" · ")}
        </p>
      )}
      <p className="mt-1 text-xs text-muted-foreground">
        {escalation.status === "pending"
          ? "Waiting — no deadline. Answer or skip below; the asking turn holds."
          : `Response: ${escalation.response ?? "(none)"}`}
      </p>
    </section>
  );
}

function IdentitySummary({ detail }: { detail: DelegationDetail }) {
  const meta: Array<[string, string | null]> = [
    ["delegation_id", detail.delegation_id],
  ];
  return (
    <section data-testid="identity-summary">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
        Identity
      </h3>
      <dl className="grid grid-cols-1 gap-1 text-xs">
        {meta.map(([k, v]) => (
          <div
            key={k}
            className="flex items-center justify-between border border-border rounded px-2 py-1"
          >
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="font-mono break-all text-right">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Transcript tab (rescoped 2a, chronological, degrades gracefully)
// ---------------------------------------------------------------------------

export function TranscriptTab({ transcript }: { transcript?: TranscriptBlock[] | null }) {
  if (!transcript || transcript.length === 0) {
    return (
      <FrozenStateNotice />
    );
  }
  return (
    <section data-testid="transcript-tab">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
        Transcript — {transcript.length} block{transcript.length === 1 ? "" : "s"}
      </h3>
      <ol className="space-y-3">
        {transcript.map((block) => (
          <TranscriptBlockView key={block.id} block={block} />
        ))}
      </ol>
    </section>
  );
}

/** Frozen-state copy (ruling 4c): replaces the dead bubble / empty
 *  promise when a running turn has produced no transcript yet. */
export function FrozenStateNotice() {
  return (
    <p
      data-testid="transcript-frozen"
      className="flex items-center gap-1.5 px-1 text-xs italic text-muted-foreground/70"
    >
      <span className="typing-dots" aria-hidden>
        <span />
        <span />
        <span />
      </span>
      No output yet — tool running, bounded.
    </p>
  );
}

function TranscriptBlockView({ block }: { block: TranscriptBlock }) {
  return (
    <li data-testid={`transcript-block-${block.id}`} className="rounded-lg border border-border/60 bg-card/60 p-2.5">
      <div className="flex items-center gap-2 pb-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          {block.role}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">{block.id}</span>
      </div>
      {block.prompt != null && block.prompt !== "" && (
        <div className="mb-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Prompt
          </div>
          <pre className="text-[11px] font-mono whitespace-pre-wrap break-words rounded bg-muted p-2 max-h-64 overflow-auto">
            {block.prompt}
          </pre>
        </div>
      )}
      {block.reasoning != null && block.reasoning !== "" && (
        <div className="mb-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Reasoning
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground break-words">
            {block.reasoning}
          </p>
        </div>
      )}
      {block.text != null && block.text !== "" && (
        <div className="mb-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Assistant
          </div>
          <Markdown source={block.text} className="text-xs" />
        </div>
      )}
      {block.tools && block.tools.length > 0 && (
        <div className="mb-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Tools
          </div>
          <ul className="space-y-2">
            {block.tools.map((t) => (
              <ToolTimelineRow key={t.callID} tool={t} />
            ))}
          </ul>
        </div>
      )}
      {block.tokens && (
        <div className="text-[10px] text-muted-foreground">
          tokens: in {block.tokens.input ?? 0} · out {block.tokens.output ?? 0}
          {block.tokens.reasoning != null ? ` · reasoning ${block.tokens.reasoning}` : ""}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Tools tab (ruling Q2 — newest-first, UI-only reverse)
// ---------------------------------------------------------------------------

export function ToolsTab({ tools }: { tools: ToolTimelineEntry[] }) {
  if (tools.length === 0) {
    return (
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          Tool timeline
        </h3>
        <p className="text-xs text-muted-foreground">No tool calls in this delegation.</p>
      </section>
    );
  }
  // Ruling Q2: the timeline surface reads newest-first (recent work on
  // top). The Transcript / status_timeline / CLI / backend order is
  // untouched — this is a UI-only .reverse() at render. The order on
  // the wire is chronological; we pin newest-first here for the audit
  // list (see __tests__/ToolsOrder.test.tsx).
  const newestFirst = [...tools].reverse();
  return (
    <section data-testid="tools-tab">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
        Tool timeline — {newestFirst.length} call{newestFirst.length === 1 ? "" : "s"} (newest first)
      </h3>
      <ul className="space-y-2">
        {newestFirst.map((t) => (
          <ToolTimelineRow key={t.callID} tool={t} />
        ))}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Prompt tab (composed / audit sections)
// ---------------------------------------------------------------------------

export function PromptTab({ composed }: { composed: ComposedPrompt | null }) {
  return <ComposedPromptSection composed={composed} />;
}

function ComposedPromptSection({ composed }: { composed: ComposedPrompt | null }) {
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
    <section data-testid="prompt-tab">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1"
      >
        {open ? <ChevronDownIcon /> : <ChevronRightIcon />}
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

// Tiny inline chevron icons (avoid an extra lucide import in the
// shared section module so the dock can reuse it with no UI deps
// beyond what it already pulls).
function ChevronDownIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}
function ChevronRightIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Tokens / Status tab (existing sections, chronological)
// ---------------------------------------------------------------------------

export function TokensStatusTab({
  tokens,
  timeline,
}: {
  tokens: DelegationDetail["tokens"];
  timeline: DelegationDetail["status_timeline"];
}) {
  return (
    <div className="space-y-4">
      <TokensSection tokens={tokens} />
      <StatusTimelineSection timeline={timeline} />
    </div>
  );
}

function TokensSection({ tokens }: { tokens: DelegationDetail["tokens"] }) {
  if (!tokens) return null;
  return (
    <section data-testid="tokens-tab">
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
    <section data-testid="status-timeline-tab">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
        Status timeline
      </h3>
      <ul className="space-y-0.5">
        {timeline.map((c, i) => (
          <li key={`${c.status}-${i}`} className="flex items-center gap-2 text-xs">
            <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground" />
            <span className="font-mono">{c.status}</span>
            {c.source && <span className="text-muted-foreground">({c.source})</span>}
            {c.ts && <span className="text-muted-foreground ml-auto">{c.ts}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
