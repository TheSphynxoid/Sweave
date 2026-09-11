/**
 * Assistant-ui Thread, Message and Composer (R4.2 step 2-pre).
 *
 * Built against the INSTALLED @assistant-ui/react 0.15.18 primitive
 * API (canonical anatomy per the registry Thread element):
 *
 *   ThreadPrimitive.Root
 *     ThreadPrimitive.Viewport (autoScroll + turnAnchor="bottom")
 *       AuiIf empty  -> Welcome (suggested prompts)
 *       AuiIf loading-> HistorySkeleton
 *       ThreadPrimitive.Messages -> {({message}) => user|assistant}
 *       ThreadPrimitive.ViewportFooter
 *         ThreadPrimitive.ScrollToBottom + Composer
 *
 * Message components read their own state via the ambient message
 * scope (`useAuiState((s) => s.message...)`); content renders through
 * `MessagePrimitive.Parts` with the Text slot. Timestamps + the
 * delegation id ride in `metadata.custom` (projected by
 * `src/lib/chat/runtime.ts`).
 *
 * Rulings (2026-09-07): the action bar is REAL affordances only
 * (copy + timestamp); edit/regenerate/fork are R4.3 (no disabled fake
 * buttons); the composer stop affordance is disabled-with-tooltip
 * (no backend cancel path yet; opencode /abort verified for R4.3).
 */

import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
  ActionBarPrimitive,
  AuiIf,
  useAuiState,
  useAui,
} from "@assistant-ui/react";
import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Bot,
  Brain,
  Check,
  Copy,
  OctagonX,
  Pencil,
  RotateCcw,
  Sparkles,
  User,
} from "lucide-react";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { Avatar } from "@/components/assistant-ui/elements/avatar";
import { Skeleton } from "@/components/assistant-ui/elements/skeleton";
import { useWS } from "@/context/WSProvider";
import { api } from "@/api/client";
import { useChatActions } from "@/lib/chat/actions";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { AssistantTextPart } from "./markdown/AssistantTextPart";
import { TurnDelegations } from "./TurnDelegations";
import { TurnQuestions } from "./TurnQuestions";
import { CopyIdBadge } from "@/components/CopyId";
import { TextShimmer } from "@/components/agent-elements/text-shimmer";
import { cn } from "@/utils/cn";

// ---------------------------------------------------------------------------
// Thread
// ---------------------------------------------------------------------------

const SUGGESTED_PROMPTS = [
  "Help me understand this codebase",
  "Write a test for the auth module",
  "Refactor the chat component",
  "Explain the delegation flow",
];

export function Thread() {
  return (
    <ThreadPrimitive.Root
      className="flex h-full min-h-0 flex-col bg-transparent"
      style={{ ["--thread-max-width" as string]: "48rem" }}
    >
      <ThreadPrimitive.Viewport
        autoScroll
        turnAnchor="bottom"
        scrollToBottomOnRunStart
        scrollToBottomOnThreadSwitch
        scrollToBottomOnInitialize
        className="flex min-h-0 flex-1 flex-col overflow-y-auto scrollbar-thin"
        data-testid="thread-viewport"
      >
        <AuiIf condition={(s) => s.thread.isEmpty && !s.thread.isLoading}>
          <Welcome prompts={SUGGESTED_PROMPTS} />
        </AuiIf>

        <AuiIf condition={(s) => s.thread.isLoading && !s.thread.isEmpty}>
          <HistorySkeleton />
        </AuiIf>

        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 pb-6 pt-4">
          <ThreadPrimitive.Messages>
            {({ message }) =>
              message.role === "user" ? <UserMessage /> : <AssistantMessage />
            }
          </ThreadPrimitive.Messages>
          <PendingTurnIndicator />
        </div>

        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 mt-auto">
          <TurnStatusBar />
          <ThreadPrimitive.ScrollToBottom asChild>
            <button
              type="button"
              aria-label="Scroll to bottom"
              data-testid="thread-scroll-to-bottom"
              className="mx-auto mb-2 grid h-8 w-8 place-items-center rounded-full border border-border bg-popover text-muted-foreground shadow-md transition-opacity hover:text-foreground disabled:opacity-0"
            >
              <ArrowDown size={15} />
            </button>
          </ThreadPrimitive.ScrollToBottom>
          <Composer />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// Pending turn indicator (the dead zone between submit and the first
// chat.delta: no streaming bubble exists yet, so show a shimmer row
// under the user's message instead of an apparently frozen thread)
// ---------------------------------------------------------------------------

function PendingTurnIndicator() {
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const messages = useAuiState((s) => s.thread.messages);
  const lastRole = messages.length ? messages[messages.length - 1]?.role : undefined;
  if (!isRunning || lastRole !== "user") return null;
  return (
    <div className="flex gap-3" data-testid="pending-turn-indicator">
      <Avatar
        size="sm"
        className="mt-0.5 border border-border bg-card"
        fallback={<Bot size={15} className="text-primary" />}
      />
      <div className="flex items-center py-2 text-sm">
        <TextShimmer className="text-muted-foreground">Thinking…</TextShimmer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Turn status bar (waiting-vs-streaming feedback).
//
// Sits above the composer while a turn is in flight and always answers
// three questions: what phase (thinking = no assistant text yet vs
// streaming = deltas arriving), how long so far (ticking elapsed, the
// liveness proof when the model warms up), and how much arrived (live
// char count). The WS connection dot explains a stalled turn when the
// socket is reconnecting (deltas can't arrive until it reopens).
// ---------------------------------------------------------------------------

function useTurnElapsed(isRunning: boolean): number {
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isRunning) {
      setStartedAt(null);
      return;
    }
    setStartedAt((prev) => prev ?? Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [isRunning]);
  if (!isRunning || startedAt === null) return 0;
  return Math.max(0, Math.floor((now - startedAt) / 1000));
}

/**
 * Seconds since the turn last produced output. `elapsed` is the
 * ticking turn age, `activeTick` the elapsed value when output last
 * grew (see TurnStatusBar). A high quiet number with a low char
 * count is the visible signature of a wedged turn (hung tool
 * approval, dead serve) -- the case the backend stall watchdog
 * fails fast on. Pure so it can be unit-tested.
 */
export function quietSeconds(elapsed: number, activeTick: number): number {
  return Math.max(0, elapsed - activeTick);
}

function threadTextOf(message: unknown): string {
  const content = (message as { content?: unknown } | null)?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter(
        (p): p is { type: string; text?: string } =>
          !!p && typeof p === "object" && (p as { type?: unknown }).type === "text",
      )
      .map((p) => p.text ?? "")
      .join("");
  }
  return "";
}

function threadCustomOf(message: unknown): SweaveCustom {
  const metadata = (message as { metadata?: unknown } | null)?.metadata;
  return (
    ((metadata as { custom?: SweaveCustom } | undefined)?.custom ?? {}) as SweaveCustom
  );
}

function TurnStatusBar() {
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const messages = useAuiState((s) => s.thread.messages);
  // The dev lab renders Thread without a WSProvider; default to
  // "open" there (no warning) instead of throwing.
  let wsState = "open";
  try {
    wsState = useWS().state;
  } catch {
    wsState = "open";
  }
  const elapsed = useTurnElapsed(isRunning);
  // Quiet tracking hooks MUST sit above the early return (Rules of
  // Hooks): the bar mounts idle (isRunning false) and starts later.
  // The pure message reads below are hook-free, so they can also
  // live up here; the return-early then only guards the JSX.
  const [activeTick, setActiveTick] = useState(0);
  const lastChars = useRef(0);
  const last = messages.length ? messages[messages.length - 1] : undefined;
  const lastStreaming =
    last !== undefined &&
    last.role === "assistant" &&
    (last as { status?: { type?: string } }).status?.type === "running";
  const chars = lastStreaming ? threadTextOf(last).length : 0;
  const delegationId = last !== undefined ? threadCustomOf(last).delegationId : null;
  const wsDown = wsState !== "open";
  // Quiet tracking: the elapsed tick at which output last grew. Any
  // delta (streaming text or thinking) moves it; a turn that stops
  // producing shows a growing "quiet Ns" next to the elapsed clock.
  useEffect(() => {
    if (elapsed < activeTick) {
      // New turn (elapsed restarted): reset the baseline so the
      // previous turn's char count can't pin quiet at zero.
      lastChars.current = 0;
      setActiveTick(0);
      return;
    }
    if (chars > lastChars.current) {
      lastChars.current = chars;
      setActiveTick(elapsed);
    }
  });
  if (!isRunning) return null;

  const quiet = quietSeconds(elapsed, activeTick);

  return (
    <div className="px-4 pb-1" data-testid="turn-status-bar">
      <div className="mx-auto flex w-full max-w-3xl items-center gap-2 rounded-lg border border-border bg-card/90 px-2.5 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur">
        <span
          aria-hidden
          className={cn(
            "h-2 w-2 shrink-0 rounded-full",
            lastStreaming ? "animate-pulse bg-primary" : "animate-pulse bg-amber-500",
          )}
        />
        <span className="font-medium text-foreground">
          {lastStreaming ? "Streaming" : "Thinking"}
        </span>
        {lastStreaming && (
          <span className="tabular-nums">
            {chars.toLocaleString()} chars
          </span>
        )}
        <span className="tabular-nums">{elapsed}s</span>
        {quiet >= 10 && (
          <span
            className="tabular-nums text-amber-600 dark:text-amber-400"
            title="No output arrived in this long -- the turn may be wedged (the backend fails it after 5 silent minutes)"
            data-testid="turn-status-quiet"
          >
            quiet {quiet}s
          </span>
        )}
        {delegationId && (
          <CopyIdBadge
            id={delegationId}
            label="Chat turn delegation"
            testId="turn-status-delegation-id"
          />
        )}
        <span className="flex-1" />
        <span
          title={wsDown ? `Live updates ${wsState} — deltas resume on reconnect` : "Live updates connected"}
          className={cn(
            "flex items-center gap-1",
            wsDown ? "font-medium text-amber-600 dark:text-amber-400" : "text-muted-foreground/70",
          )}
          data-testid="turn-status-ws"
          data-ws-state={wsState}
        >
          <span
            aria-hidden
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              wsDown ? "bg-amber-500" : "bg-emerald-500",
            )}
          />
          {wsDown ? "reconnecting" : "live"}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Welcome (empty-thread state with suggested prompts)
// ---------------------------------------------------------------------------

function Welcome({ prompts }: { prompts: string[] }) {
  const aui = useAui();
  return (
    <div
      data-testid="welcome-screen"
      className="mx-auto my-auto flex w-full max-w-2xl flex-col items-center px-4 py-10"
    >
      <div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl border border-border bg-card shadow-sm">
        <Sparkles size={24} className="text-primary" />
      </div>
      <h2 className="text-lg font-semibold tracking-tight">Sweave orchestrator</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Start a conversation or pick a suggested prompt.
      </p>
      <div className="mt-6 grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
        {prompts.map((prompt, i) => (
          <button
            key={i}
            type="button"
            data-testid={`suggested-prompt-${i}`}
            onClick={() => aui.thread.append(prompt)}
            className="rounded-xl border border-border bg-card px-3.5 py-3 text-left text-sm text-foreground/90 transition-colors hover:border-ring hover:bg-accent"
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// History loading skeleton (thread switch)
// ---------------------------------------------------------------------------

function HistorySkeleton() {
  return (
    <div
      data-testid="history-skeleton"
      className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6"
      aria-busy="true"
    >
      <div className="flex gap-3">
        <Skeleton className="h-8 w-8 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      </div>
      <div className="flex justify-end">
        <Skeleton className="h-10 w-1/2 rounded-2xl" />
      </div>
      <div className="flex gap-3">
        <Skeleton className="h-8 w-8 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message metadata (projected via metadata.custom)
// ---------------------------------------------------------------------------

interface SweaveCustom {
  timestamp?: string | null;
  delegationId?: string | null;
  superseded?: boolean;
  /** Live or persisted reasoning text (chat.thinking / metadata.thinking). */
  thinking?: string | null;
  /** Multi-message turns (2026-09-11): orchestrator round (0 = first
      turn, 1 = synthesis). Absent on legacy messages — readers treat
      it as 0. */
  round?: number | null;
  /** False only for intermediate round messages, which render
      collapsed (RoundBlock) instead of inline. Absent means final. */
  turnFinal?: boolean | null;
}

function useMessageCustom(): SweaveCustom {
  const metadata = useAuiState((s) => s.message.metadata);
  return ((metadata as { custom?: SweaveCustom } | undefined)?.custom ?? {}) as SweaveCustom;
}

function formatTimestamp(ts: string | null | undefined): string | null {
  if (!ts) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// ---------------------------------------------------------------------------
// Assistant message (avatar + header + markdown body + footer/action bar)
// ---------------------------------------------------------------------------

function AssistantMessage() {
  const custom = useMessageCustom();
  const message = useAuiState((s) => s.message);
  const messageId = useAuiState((s) => s.message.id);
  const isRunning = useAuiState((s) => s.message.status?.type === "running");
  const time = formatTimestamp(custom.timestamp);

  const body = (
    <>
      {custom.thinking ? (
        <ThinkingBlock thinking={custom.thinking} streaming={isRunning} />
      ) : null}
      <div className="text-sm leading-relaxed">
        <MessagePrimitive.Parts components={{ Text: AssistantTextPart }} />
        {isRunning && <span className="streaming-cursor" aria-hidden />}
      </div>

      {custom.delegationId && custom.turnFinal !== false && (
        <TurnQuestions delegationId={custom.delegationId} />
      )}

      {custom.delegationId && custom.turnFinal !== false && (
        <TurnDelegations parentDelegationId={custom.delegationId} />
      )}

      <div className="mt-1.5 flex items-center justify-between gap-2">
        {time && <time className="text-[11px] text-muted-foreground/70">{time}</time>}
        <div className="flex-1" />
        <AssistantActionBar />
      </div>
    </>
  );

  // Intermediate round messages (a defer turn's narration before the
  // final synthesis) render collapsed but present — the transcript
  // never loses a round. Final + legacy messages render inline.
  const roundShell =
    custom.turnFinal === false ? (
      <RoundBlock
        round={typeof custom.round === "number" ? custom.round : 0}
        preview={threadTextOf(message)}
      >
        {body}
      </RoundBlock>
    ) : (
      body
    );

  return (
    <div
      className="group/message flex gap-3"
      data-testid="assistant-message-row"
      data-message-id={messageId}
    >
      <Avatar
        size="sm"
        className="mt-0.5 border border-border bg-card"
        fallback={<Bot size={15} className="text-primary" />}
      />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2 text-xs">
          <span className="font-medium text-foreground">Assistant</span>
          {custom.delegationId && (
            <CopyIdBadge
              id={custom.delegationId}
              label="Chat turn delegation"
              testId="delegation-id-badge"
            />
          )}
        </div>

        {custom.superseded ? (
          <SupersededBlock label="Superseded" preview={threadTextOf(message)}>
            {roundShell}
          </SupersededBlock>
        ) : (
          roundShell
        )}
      </div>
    </div>
  );
}

/**
 * Collapsed shell for an intermediate round message (multi-message
 * turns, 2026-09-11). Unlike SupersededBlock this is live history,
 * not rewound history: neutral (not dimmed), collapsed by default,
 * click to expand the round's narration in place. Exported for the
 * round-block unit test (LiveTree KindPill/StatusPill precedent).
 */
export function RoundBlock({
  round,
  preview,
  children,
}: {
  round: number;
  preview: string;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div data-testid="round-block" data-round={round}>
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className="flex max-w-full items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1 text-left text-[11px] text-muted-foreground hover:text-foreground"
      >
        <span className="shrink-0 rounded bg-muted px-1 py-px font-medium">
          Round {round + 1}
        </span>
        <span className="truncate">{preview.slice(0, 80) || "—"}</span>
      </button>
      {expanded && <div className="mt-1.5">{children}</div>}
    </div>
  );
}

/**
 * Collapsed shell for a superseded message (edit + resend / retry
 * rewound past it). Record, not deletion: dimmed one-liner, click
 * to expand the original content in place.
 */
function SupersededBlock({
  label,
  preview,
  children,
}: {
  label: string;
  preview: string;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="opacity-60" data-testid="superseded-block">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className="flex max-w-full items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1 text-left text-[11px] text-muted-foreground hover:text-foreground"
      >
        <span className="shrink-0 rounded bg-muted px-1 py-px font-medium">{label}</span>
        <span className="truncate">{preview.slice(0, 80) || "—"}</span>
      </button>
      {expanded && <div className="mt-1.5 opacity-100">{children}</div>}
    </div>
  );
}

/**
 * Thinking block (reasoning capture).
 *
 * While the turn streams, the block is expanded and live (the
 * provider's reasoning increments arrive as chat.thinking events).
 * Once finalized, it collapses into a <details> shell so the
 * reasoning stays inspectable without dominating the bubble. The
 * persisted copy rides on message metadata.thinking, so reloads
 * keep it.
 */
function ThinkingBlock({
  thinking,
  streaming,
}: {
  thinking: string;
  streaming: boolean;
}) {
  if (streaming) {
    return (
      <div
        data-testid="thinking-block"
        data-state="streaming"
        className="mb-2 rounded-lg border border-border bg-muted/40 px-3 py-2"
      >
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
          <Brain size={12} className="text-primary" />
          <span>Thinking…</span>
        </div>
        <div className="max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
          {thinking}
        </div>
      </div>
    );
  }
  return (
    <details
      data-testid="thinking-block"
      data-state="done"
      className="mb-2 rounded-lg border border-border bg-muted/40 px-3 py-1.5"
    >
      <summary className="flex cursor-pointer items-center gap-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground">
        <Brain size={12} className="text-primary" />
        <span>Thinking</span>
      </summary>
      <div className="mt-1.5 max-h-60 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
        {thinking}
      </div>
    </details>
  );
}

/**
 * Copy + retry (both REAL affordances). The Root unmounts itself on
 * non-last messages and while the run is in flight (hideWhenRunning
 * + autohide="not-last"), so retry only ever targets the latest
 * assistant reply of an idle thread — and no hover CSS is needed.
 */
function AssistantActionBar() {
  const isCopied = useAuiState((s) => s.message.isCopied);
  const messageId = useAuiState((s) => s.message.id);
  const custom = useMessageCustom();
  const messages = useAuiState((s) => s.thread.messages);
  const actions = useChatActions();

  const retry = async () => {
    // The rerun target is the nearest preceding user message.
    const idx = messages.findIndex((m) => m.id === messageId);
    let userId: string | null = null;
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        userId = messages[i].id;
        break;
      }
    }
    if (!userId || !actions) return;
    // A rerun re-drives the orchestrator, which may defer AGAIN —
    // confirm when the old turn is known to have spawned children.
    if (custom.delegationId) {
      try {
        const kids = await api.listDelegations({ parent_task_id: custom.delegationId });
        if (
          kids.length > 0 &&
          !window.confirm(
            `This turn created ${kids.length} specialist task(s). ` +
              `Retrying may duplicate that work (the old tasks stay on record). Retry anyway?`,
          )
        ) {
          return;
        }
      } catch {
        // The children check is advisory; a failed check proceeds.
      }
    }
    actions.rerun(userId);
  };

  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="flex items-center gap-0.5"
      data-testid="action-bar"
    >
      <TooltipIconButton
        tooltip="Retry turn"
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => void retry()}
      >
        <RotateCcw size={13} />
      </TooltipIconButton>
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton
          tooltip={isCopied ? "Copied" : "Copy"}
          variant="ghost"
          size="sm"
          className="h-7 w-7"
        >
          {isCopied ? <Check size={13} className="text-primary" /> : <Copy size={13} />}
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
    </ActionBarPrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// User message (right-aligned bubble + plain text)
// ---------------------------------------------------------------------------

function UserPlainText({
  text,
  part,
}: {
  text?: string;
  part?: { text?: string };
}) {
  const value = text ?? part?.text ?? "";
  return <span className="whitespace-pre-wrap">{value}</span>;
}

function UserMessage() {
  const custom = useMessageCustom();
  const message = useAuiState((s) => s.message);
  const messageId = useAuiState((s) => s.message.id);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const time = formatTimestamp(custom.timestamp);
  const actions = useChatActions();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const startEdit = () => {
    setDraft(threadTextOf(message));
    setEditing(true);
  };
  const saveEdit = () => {
    const text = draft.trim();
    setEditing(false);
    if (!text || !actions) return;
    actions.rerun(messageId, text);
  };

  const bubble = editing ? (
    <div className="w-full min-w-[16rem]">
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={3}
        autoFocus
        data-testid="user-message-edit-input"
        className="w-full resize-y rounded-xl border border-ring bg-card px-3 py-2 text-sm text-foreground focus:outline-none"
      />
      <div className="mt-1 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={() => setEditing(false)}
          className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={saveEdit}
          data-testid="user-message-edit-save"
          className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
        >
          Save &amp; resend
        </button>
      </div>
    </div>
  ) : (
    <div className="rounded-2xl border border-primary/20 bg-primary/10 px-3.5 py-2 text-sm shadow-sm transition-shadow group-hover/message:shadow-md">
      <MessagePrimitive.Parts components={{ Text: UserPlainText }} />
    </div>
  );

  return (
    <div
      className="group/message flex justify-end"
      data-testid="user-message-row"
      data-message-id={messageId}
    >
      <div className="flex max-w-[75%] items-end gap-2">
        <div className="flex min-w-0 flex-col items-end gap-1">
          {custom.superseded ? (
            <SupersededBlock label="Superseded" preview={threadTextOf(message)}>
              {bubble}
            </SupersededBlock>
          ) : (
            bubble
          )}
          <div className="flex items-center gap-1 pr-1">
            {time && <time className="text-[11px] text-muted-foreground/70">{time}</time>}
            {!isRunning && !editing && actions && (
              <button
                type="button"
                onClick={startEdit}
                title="Edit and resend"
                aria-label="Edit and resend"
                data-testid="user-message-edit"
                className="rounded p-0.5 text-muted-foreground/60 opacity-0 transition-opacity hover:text-foreground focus:opacity-100 group-hover/message:opacity-100"
              >
                <Pencil size={12} />
              </button>
            )}
          </div>
        </div>
        <Avatar
          size="sm"
          className="border border-border bg-card"
          fallback={<User size={15} className="text-muted-foreground" />}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composer (Enter sends / Shift+Enter newline via ComposerPrimitive;
// stop affordance disabled-with-tooltip per the 2026-09-07 ruling)
// ---------------------------------------------------------------------------

function Composer() {
  const isEmpty = useAuiState((s) => s.composer.isEmpty);
  const isRunning = useAuiState((s) => s.thread.isRunning);

  return (
    <div className="px-4 pb-4">
      <ComposerPrimitive.Root
        className="mx-auto flex w-full max-w-3xl items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-lg transition-[border-color,box-shadow] focus-within:border-primary/70 focus-within:ring-2 focus-within:ring-ring/30"
        data-testid="chat-composer"
      >
        <ComposerPrimitive.Input
          placeholder={isRunning ? "The orchestrator is replying…" : "Write a message…"}
          autoFocus
          rows={1}
          data-testid="chat-composer-input"
          className="max-h-40 min-h-[38px] flex-1 resize-none bg-transparent px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
        />
        {isRunning ? (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  disabled
                  data-testid="chat-composer-stop"
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground opacity-60"
                >
                  <OctagonX size={16} />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top">Stop lands with R4.3 (cancel path)</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : (
          <ComposerPrimitive.Send asChild>
            <button
              type="button"
              aria-label="Send message"
              data-testid="chat-composer-send"
              className={cn(
                "grid h-9 w-9 shrink-0 place-items-center rounded-xl transition-colors",
                "bg-primary text-primary-foreground hover:bg-primary/90",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                "disabled:cursor-not-allowed disabled:opacity-40",
              )}
              disabled={isEmpty}
            >
              <ArrowUp size={16} />
            </button>
          </ComposerPrimitive.Send>
        )}
      </ComposerPrimitive.Root>
      <p className="mx-auto mt-1.5 w-full max-w-3xl text-center text-[10px] text-muted-foreground/60">
        Enter to send · Shift+Enter for a newline
      </p>
    </div>
  );
}
