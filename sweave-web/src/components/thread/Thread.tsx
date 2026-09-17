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
 * Chat polish (2026-09-13): agentic hierarchy + smooth motion.
 * User turns are gradient bubbles; orchestrator turns are elevated
 * cards with a gradient-ring avatar, shimmer thinking states, a
 * floating turn-status pill, and entrance animations. All primitive
 * structure + testids are unchanged (see the thread test suite).
 *
 * Rulings (2026-09-07): the action bar is REAL affordances only
 * (copy + timestamp); edit/regenerate/fork are R4.3 (no disabled fake
 * buttons). Amended 2026-09-14: the composer stop affordance is live
 * (POSTs the turn-cancel endpoint; the server stops the whole
 * subtree and keeps the partial reply as a `cancelled` bubble).
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
  ArrowRight,
  AlertTriangle,
  Bot,
  Brain,
  Check,
  ChevronDown,
  Copy,
  Loader2,
  OctagonX,
  Pencil,
  RotateCcw,
  Sparkles,
  User,
  Zap,
} from "lucide-react";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { Avatar } from "@/components/assistant-ui/elements/avatar";
import { Skeleton } from "@/components/assistant-ui/elements/skeleton";
import { EditTool } from "@/components/agent-elements/tools/edit-tool";
import type { ChatSegment, ChatToolRow } from "@/types";
import { useWS } from "@/context/WSProvider";
import { api } from "@/api/client";
import { useChatActions } from "@/lib/chat/actions";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { AssistantTextPart } from "./markdown/AssistantTextPart";
import { Markdown } from "./markdown/Markdown";
import { TurnDelegations } from "./TurnDelegations";
import { TurnQuestions } from "./TurnQuestions";
import { ToolDetailMeta } from "./ToolDetailMeta";
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

const SUGGESTED_ICONS = [Sparkles, Zap, Pencil, Bot] as const;

export function Thread() {
  return (
    <ThreadPrimitive.Root
      className="chat-thread-ambient flex h-full min-h-0 flex-col"
      style={{ ["--thread-max-width" as string]: "48rem" }}
    >
      <ThreadPrimitive.Viewport
        autoScroll
        turnAnchor="bottom"
        scrollToBottomOnRunStart
        scrollToBottomOnThreadSwitch
        scrollToBottomOnInitialize
        className="thread-viewport-scroll flex min-h-0 flex-1 flex-col overflow-y-auto scrollbar-thin"
        data-testid="thread-viewport"
      >
        <AuiIf condition={(s) => s.thread.isEmpty && !s.thread.isLoading}>
          <Welcome prompts={SUGGESTED_PROMPTS} />
        </AuiIf>

        <AuiIf condition={(s) => s.thread.isLoading && !s.thread.isEmpty}>
          <HistorySkeleton />
        </AuiIf>

        <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 pb-6 pt-4">
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
              className="mx-auto mb-2 grid h-8 w-8 place-items-center rounded-full border border-border/70 bg-popover/95 text-muted-foreground shadow-lg backdrop-blur transition-all hover:-translate-y-0.5 hover:text-foreground hover:shadow-xl disabled:opacity-0"
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
// chat.delta: no streaming bubble exists yet, so show an agentic
// "orchestrator at work" row under the user's message instead of an
// apparently frozen thread)
// ---------------------------------------------------------------------------

function PendingTurnIndicator() {
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const messages = useAuiState((s) => s.thread.messages);
  const lastRole = messages.length ? messages[messages.length - 1]?.role : undefined;
  if (!isRunning || lastRole !== "user") return null;
  return (
    <div
      className="animate-message-in flex gap-3"
      data-testid="pending-turn-indicator"
    >
      <span className="animate-presence mt-0.5 rounded-full bg-gradient-to-br from-primary via-primary/50 to-transparent p-[1.5px]">
        <Avatar
          size="sm"
          className="border-0 bg-card"
          fallback={<Bot size={15} className="text-primary" />}
        />
      </span>
      <div className="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-border/60 bg-card/70 px-4 py-3 shadow-sm backdrop-blur">
        <div className="flex items-center gap-2 text-sm">
          <span className="typing-dots" aria-hidden>
            <span />
            <span />
            <span />
          </span>
          <TextShimmer className="font-medium">Orchestrator is thinking</TextShimmer>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Decomposing your request — specialists stand by for delegation.
        </p>
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
    <div className="animate-fade-in px-4 pb-2" data-testid="turn-status-bar">
      <div className="mx-auto flex w-full max-w-3xl items-center gap-2 rounded-full border border-border/70 bg-card/90 py-1.5 pl-2.5 pr-3 text-xs text-muted-foreground shadow-lg backdrop-blur">
        <span
          aria-hidden
          className={cn(
            "grid h-6 w-6 shrink-0 place-items-center rounded-full",
            lastStreaming
              ? "bg-primary/15 text-primary"
              : "bg-amber-500/15 text-amber-600 dark:text-amber-400",
          )}
        >
          {lastStreaming ? (
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
          ) : (
            <Brain size={13} className="animate-pulse" />
          )}
        </span>
        <span className="font-semibold text-foreground">
          {lastStreaming ? "Streaming" : "Thinking"}
        </span>
        {lastStreaming && (
          <span className="tabular-nums text-muted-foreground">
            {chars.toLocaleString()} chars
          </span>
        )}
        <span className="tabular-nums text-muted-foreground">{elapsed}s</span>
        {quiet >= 10 && (
          <span
            className="rounded-full bg-amber-500/15 px-2 py-0.5 tabular-nums text-amber-600 dark:text-amber-400"
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
            "flex items-center gap-1.5",
            wsDown ? "font-medium text-amber-600 dark:text-amber-400" : "text-muted-foreground/70",
          )}
          data-testid="turn-status-ws"
          data-ws-state={wsState}
        >
          <span
            aria-hidden
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              wsDown ? "animate-pulse bg-amber-500" : "bg-emerald-500",
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
      className="chat-hero-orb mx-auto my-auto flex w-full max-w-2xl flex-col items-center px-4 py-10"
    >
      <div className="animate-presence mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-primary via-primary/70 to-primary/30 text-primary-foreground shadow-lg shadow-primary/25">
        <Sparkles size={24} />
      </div>
      <p className="mb-1 flex items-center gap-1.5 rounded-full border border-primary/25 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
        Agentic swarm ready
      </p>
      <h2 className="text-xl font-semibold tracking-tight">Sweave orchestrator</h2>
      <p className="mt-1 max-w-md text-center text-sm text-muted-foreground">
        Describe the outcome — the orchestrator decomposes it, delegates to
        specialists, and reports back here.
      </p>
      <div className="mt-6 grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
        {prompts.map((prompt, i) => {
          const Icon = SUGGESTED_ICONS[i % SUGGESTED_ICONS.length];
          return (
            <button
              key={i}
              type="button"
              data-testid={`suggested-prompt-${i}`}
              onClick={() => aui.thread.append(prompt)}
              className="group flex items-center gap-2.5 rounded-2xl border border-border/70 bg-card/80 px-3.5 py-3 text-left text-sm text-foreground/90 shadow-sm backdrop-blur transition-all hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-md"
            >
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary transition-colors group-hover:bg-primary/20">
                <Icon size={15} />
              </span>
              <span className="min-w-0 flex-1">{prompt}</span>
              <ArrowRight
                size={14}
                className="shrink-0 text-muted-foreground/50 transition-all group-hover:translate-x-0.5 group-hover:text-primary"
              />
            </button>
          );
        })}
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
  /** Arrival-ordered text/reasoning/tool segments (metadata.segments
      or the live op log). Renders think/act/think turns in stream
      order; absent on legacy messages, which keep the single thinking
      block + body (+ the TurnTools fallback for legacy tool rows). */
  segments?: ChatSegment[] | null;
  /** Compact tool rows (chat.tool live / metadata.tools persisted).
      Renders the turn's activity above the answer; absent on legacy
      messages. */
  tools?: ChatToolRow[] | null;
  /** Multi-message turns (2026-09-11): orchestrator round (0 = first
      turn, 1 = synthesis). Absent on legacy messages — readers treat
      it as 0. */
  round?: number | null;
  /** False only for intermediate round messages, which render
      collapsed (RoundBlock) instead of inline. Absent means final. */
  turnFinal?: boolean | null;
  /** True while this message belongs to the turn still running on
      the session (projected from the adapter's turn state, not
      message finality). Keeps the children/question lanes mounted
      across the round-0 → synthesis gap and auto-expands the
      intermediate round while its turn is live. */
  isActiveTurn?: boolean | null;
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

  // Lanes stay mounted while this message's turn is live — not just
  // when the message is final. The old turnFinal-only gate unmounted
  // the specialist box exactly when specialists started (round 0
  // persists intermediate before the child wait), forcing a reload
  // to see it again.
  const showLanes =
    custom.turnFinal !== false || isRunning || custom.isActiveTurn === true;

  // Lane dedupe (2026-09-17): rounds share one chat delegation id,
  // so without this the same activity box renders under the round-0
  // message AND the round-1 synthesis. Children/questions belong to
  // the spawning round (round 0 today — sequential waves will stamp
  // the round at spawn per WAVE_LOOP_PLAN.md ruling 4); only that
  // round's message mounts the lanes.
  const isSpawnRound = (custom.round ?? 0) === 0;

  const body = (
    <>
      {custom.segments && custom.segments.length > 0 ? (
        <SegmentedBody
          segments={custom.segments}
          streaming={isRunning}
          tools={custom.tools ?? []}
        />
      ) : (
        <>
          {custom.tools && custom.tools.length > 0 ? (
            <TurnTools tools={custom.tools} streaming={isRunning} />
          ) : null}
          {custom.thinking ? (
            <ThinkingBlock thinking={custom.thinking} streaming={isRunning} />
          ) : null}
          <div className="text-sm leading-relaxed">
            <MessagePrimitive.Parts components={{ Text: AssistantTextPart }} />
            {isRunning && <span className="streaming-cursor" aria-hidden />}
          </div>
        </>
      )}

      {custom.delegationId && showLanes && isSpawnRound && (
        <TurnQuestions delegationId={custom.delegationId} />
      )}

      {custom.delegationId && showLanes && isSpawnRound && (
        <TurnDelegations parentDelegationId={custom.delegationId} />
      )}

      <div className="mt-2 flex items-center justify-between gap-2 border-t border-border/40 pt-1.5">
        {time ? (
          <time className="text-[11px] tabular-nums text-muted-foreground/70">{time}</time>
        ) : (
          <span />
        )}
        <AssistantActionBar />
      </div>
    </>
  );

  // Intermediate round messages (a defer turn's narration before the
  // final synthesis) render collapsed but present — the transcript
  // never loses a round. Final + legacy messages render inline.
  // The block auto-expands while its turn is live (so the children
  // lane stays visible across the child-wait gap) and collapses on
  // settle; a manual toggle always wins.
  const roundShell =
    custom.turnFinal === false ? (
      <RoundBlock
        round={typeof custom.round === "number" ? custom.round : 0}
        preview={threadTextOf(message)}
        active={custom.isActiveTurn === true}
      >
        {body}
      </RoundBlock>
    ) : (
      body
    );

  return (
    <div
      className="animate-message-in group/message flex gap-3"
      data-testid="assistant-message-row"
      data-message-id={messageId}
    >
      <span
        className={cn(
          "mt-0.5 h-fit rounded-full bg-gradient-to-br from-primary via-primary/50 to-transparent p-[1.5px]",
          isRunning && "animate-presence",
        )}
      >
        <Avatar
          size="sm"
          className="border-0 bg-card"
          fallback={<Bot size={15} className="text-primary" />}
        />
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span className="flex items-center gap-1 font-semibold text-foreground">
            <Sparkles size={11} className="text-primary" />
            Sweave orchestrator
          </span>
          {isRunning && (
            <span className="flex items-center gap-1 rounded-full bg-primary/10 px-2 py-px text-[10px] font-medium text-primary">
              <Loader2 size={10} className="animate-spin" />
              working
            </span>
          )}
          {custom.delegationId && (
            <CopyIdBadge
              id={custom.delegationId}
              label="Chat turn delegation"
              testId="delegation-id-badge"
            />
          )}
        </div>

        <div
          className={cn(
            "rounded-2xl rounded-tl-md border border-border/60 bg-card/80 px-4 py-3 shadow-sm backdrop-blur transition-shadow",
            isRunning
              ? "border-primary/30 shadow-md shadow-primary/5"
              : "group-hover/message:shadow-md",
          )}
        >
          {custom.superseded ? (
            <SupersededBlock label="Superseded" preview={threadTextOf(message)}>
              {roundShell}
            </SupersededBlock>
          ) : (
            roundShell
          )}
        </div>
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
  active,
  children,
}: {
  round: number;
  preview: string;
  /** True while the round's turn is still live: auto-expands (a
      manual toggle always wins and survives settle). */
  active?: boolean;
  children: React.ReactNode;
}) {
  const [manual, setManual] = useState<boolean | null>(null);
  const expanded = manual ?? active ?? false;
  return (
    <div data-testid="round-block" data-round={round} data-active={active === true}>
      <button
        type="button"
        onClick={() => setManual(!expanded)}
        aria-expanded={expanded}
        className="flex max-w-full items-center gap-1.5 rounded-full border border-primary/25 bg-primary/[0.07] px-2.5 py-1 text-left text-[11px] text-muted-foreground transition-all hover:border-primary/50 hover:text-foreground hover:shadow-sm"
      >
        <span className="shrink-0 rounded-full bg-primary/15 px-1.5 py-px font-semibold text-primary">
          Round {round + 1}
        </span>
        <span className="truncate">{preview.slice(0, 80) || "—"}</span>
      </button>
      {expanded && (
        <div
          className="animate-fade-in mt-2 border-l-2 border-primary/30 pl-3"
          data-testid="round-block-expanded"
        >
          {children}
        </div>
      )}
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
        className="flex max-w-full items-center gap-1.5 rounded-full border border-dashed border-border px-2.5 py-1 text-left text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <span className="shrink-0 rounded-full bg-muted px-1.5 py-px font-medium">{label}</span>
        <span className="truncate">{preview.slice(0, 80) || "—"}</span>
      </button>
      {expanded && <div className="animate-fade-in mt-1.5 opacity-100">{children}</div>}
    </div>
  );
}

/**
 * Ordered segment body (interleave fidelity).
 *
 * Groups contiguous same-kind text/reasoning runs and renders them in
 * stream order with tool rows inline at their arrival positions — so
 * think, read, think, answer reads in that order instead of
 * collapsing to one Thinking blob + one answer + a detached activity
 * block. Used whenever the message carries segments (new turns AND
 * live bubbles via the adapter op log); legacy messages keep the
 * single-block path above. Exported for the segment-order unit test
 * (RoundBlock precedent).
 */
export function SegmentedBody({
  segments,
  streaming,
  tools,
}: {
  segments: ChatSegment[];
  streaming: boolean;
  tools?: ChatToolRow[];
}) {
  const byId = new Map((tools ?? []).map((t) => [t.callID, t]));
  const groups: Array<
    | { kind: "thinking"; text: string }
    | { kind: "text"; text: string }
    | { kind: "tool"; row: ChatToolRow }
  > = [];
  for (const seg of segments) {
    if (seg.kind === "tool") {
      const row = byId.get(seg.callID);
      // A marker without its row is a backend bug; drop it rather
      // than render a hole in the timeline.
      if (row) groups.push({ kind: "tool", row });
      continue;
    }
    const last = groups[groups.length - 1];
    if (
      last &&
      (last.kind === "text" || last.kind === "thinking") &&
      last.kind === seg.kind
    ) {
      last.text += seg.text;
    } else {
      groups.push({ kind: seg.kind, text: seg.text });
    }
  }
  // Track read windows so a repeated (path, range) read can show the
  // "same window" affordance (plan F3) without re-keying the backend.
  // Mark a read as sameWindow only on its SECOND+ occurrence (the
  // first sighting is the canonical window).
  const seenWindows = new Set<string>();
  return (
    <>
      {groups.map((g, i) =>
        g.kind === "thinking" ? (
          <ThinkingBlock key={i} thinking={g.text} streaming={streaming} />
        ) : g.kind === "tool" ? (
          <div key={i} className="mb-1.5">
            {isEditRow(g.row) ? (
              <EditTool part={toToolEditPart(g.row)} isCollapsible />
            ) : (
              <ToolRow
                row={g.row}
                streaming={streaming}
                sameWindow={
                  (g.row.tool || "").toLowerCase() === "read" &&
                  (() => {
                    const wkey = readWindowKey(g.row);
                    if (!wkey) return false;
                    const dup = seenWindows.has(wkey);
                    seenWindows.add(wkey);
                    return dup;
                  })()
                }
              />
            )}
          </div>
        ) : (
          <div key={i} className="text-sm leading-relaxed">
            {streaming ? (
              // While streaming, raw text verbatim (AssistantTextPart
              // rule): partial markdown — an unclosed fence, half a
              // table — renders visibly-empty or structurally-odd
              // under react-markdown, which reads as "streaming but
              // no text". The Markdown pass applies on completion.
              <div
                data-testid="assistant-streaming-plain"
                className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground"
              >
                {g.text}
              </div>
            ) : (
              <Markdown source={g.text} />
            )}
            {streaming && i === groups.length - 1 && (
              <span className="streaming-cursor" aria-hidden />
            )}
          </div>
        ),
      )}
    </>
  );
}
/**
 * Turn tool activity, legacy fallback (chat transparency).
 *
 * Renders only for messages WITHOUT segments (pre-interleave rows):
 * a summary block above the answer. Segmented turns render their
 * rows inline at arrival positions via SegmentedBody instead.
 *
 * Exported for the unit test (RoundBlock precedent).
 */
export function TurnTools({
  tools,
  streaming,
}: {
  tools: ChatToolRow[];
  streaming: boolean;
}) {
  if (tools.length === 0) return null;
  const seen = new Set<string>();
  return (
    <div className="mb-2.5 space-y-1" data-testid="turn-tools">
      {tools.map((t) => {
        const isRead = (t.tool || "").toLowerCase() === "read";
        const key = readWindowKey(t);
        const sameWindow = isRead && !!key && seen.has(key);
        if (key) seen.add(key);
        return isEditRow(t) ? (
          <div key={t.callID} data-testid={`tool-row-${t.callID}`}>
            <EditTool part={toToolEditPart(t)} isCollapsible />
          </div>
        ) : (
          <ToolRow key={t.callID} row={t} streaming={streaming} sameWindow={sameWindow} />
        );
      })}
    </div>
  );
}

/** Capitalized verb for a tool row (`read` -> `Read`). */
function toolVerb(tool: string): string {
  const name = (tool || "tool").trim() || "tool";
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** One-liner detail text: summary (path/command/pattern), else title. */
function toolDetail(row: ChatToolRow): string {
  if (row.summary) return row.summary;
  if (row.title) return row.title;
  return "";
}

/**
 * TOOL_CARDS step 2 — enriched one-liner (plan §3 step 2 + F6).
 *
 * The collapsed row MUST stay one line tall (minimal-clutter rule), so
 * the one-liner gains the audit-ready parameters derived from `detail`
 * when present, and falls back to the legacy `summary`/`title` when the
 * server is pre step-1 (no `detail` key — the degrade contract). Never
 * dumps result content: read window, write mode, bash command, grep
 * pattern+count, glob count, git verb, todo titles only.
 */
function enrichedOneLiner(row: ChatToolRow): string {
  const d = row.detail;
  switch ((row.tool || "").toLowerCase()) {
    case "read": {
      if (d?.window && d.window.shownFrom != null && d.window.shownTo != null) {
        const total = d.window.total != null ? `/${d.window.total}` : "";
        return `${row.summary || "(file)"} · L${d.window.shownFrom}–${d.window.shownTo}${total}`;
      }
      return toolDetail(row);
    }
    case "write": {
      if (d?.mode) {
        const added = d.linesAdded != null ? ` +${d.linesAdded}` : "";
        const removed = d.linesRemoved != null ? ` −${d.linesRemoved}` : "";
        const verb = d.mode === "overwrite" ? "overwrote" : "created";
        return `${row.summary || "(file)"} · ${verb}${added}${removed}`;
      }
      return toolDetail(row);
    }
    case "edit": {
      if (d?.linesAdded != null || d?.linesRemoved != null) {
        const added = d.linesAdded != null ? ` +${d.linesAdded}` : "";
        const removed = d.linesRemoved != null ? ` −${d.linesRemoved}` : "";
        return `${row.summary || "(file)"} · edited${added}${removed}`;
      }
      return toolDetail(row);
    }
    case "bash":
      // Command ALWAYS visible (F2); `summary` already carries it for
      // legacy rows, so the enriched one-liner is just the command.
      return toolDetail(row);
    case "grep": {
      if (d?.pattern != null) {
        const where = d.path ? ` in ${d.path}` : "";
        const inc = d.include ? ` (${d.include})` : "";
        const n = d.matchCount != null ? ` (${d.matchCount})` : "";
        return `“${d.pattern}”${where}${inc}${n}`;
      }
      return toolDetail(row);
    }
    case "glob": {
      if (d?.pattern != null) {
        const n = d.count != null ? ` (${d.count})` : "";
        return `${d.pattern}${n}`;
      }
      return toolDetail(row);
    }
    case "git": {
      if (d?.verb != null) {
        const args = Array.isArray(d.args) ? d.args.join(" ") : d.args ? String(d.args) : "";
        return `${d.verb}${args ? ` ${args}` : ""}`;
      }
      return toolDetail(row);
    }
    case "todo": {
      if (d?.titles && d.titles.length) {
        const more = d.titles.length > 1 ? ` +${d.titles.length - 1}` : "";
        return `${d.titles[0]}${more}`;
      }
      return toolDetail(row);
    }
    default:
      return toolDetail(row);
  }
}

/** Whether the row has detail worth revealing in an expander panel. */
function hasExpandableDetail(row: ChatToolRow): boolean {
  const d = row.detail;
  if (!d) return false;
  // Same-window badge is shown inline (no expander needed); the rest
  // reveal the source parameters / output when there is something to
  // show beyond the one-liner.
  if (d.command != null && d.command !== "") return true;
  if (d.output_excerpt != null && d.output_excerpt !== "") return true;
  if (d.preview != null && d.preview !== "") return true;
  if (d.old_capture != null && d.old_capture !== "") return true;
  if (d.matchCount != null) return true;
  if (d.count != null) return true;
  if (d.linesAdded != null || d.linesRemoved != null) return true;
  if (d.titles && d.titles.length) return true;
  return false;
}

function ToolRow({
  row,
  streaming,
  sameWindow = false,
}: {
  row: ChatToolRow;
  streaming: boolean;
  /** Read rows only: this read repeats a prior read's (path, window). */
  sameWindow?: boolean;
}) {
  const detail = enrichedOneLiner(row);
  const live = streaming && (row.status === "pending" || row.status === "running");
  const expandable = hasExpandableDetail(row);
  return (
    <ToolRowShell
      row={row}
      live={live}
      detail={detail}
      expandable={expandable}
      sameWindow={sameWindow}
    />
  );
}

/** The "same window" key for a read row (plan F3): path + shown range. */
function readWindowKey(row: ChatToolRow): string | null {
  if ((row.tool || "").toLowerCase() !== "read") return null;
  const w = row.detail?.window;
  if (!w || w.shownFrom == null || w.shownTo == null) return null;
  return `${row.summary || ""}#${w.shownFrom}-${w.shownTo}`;
}

/**
 * Row shell: the one-liner (same height as today — minimal-clutter
 * rule) plus an OPTIONAL collapsible panel below that reveals the
 * enriched detail (bash response 2K inline, write preview/diff,
 * grep/glob counts, git args, todo titles). The collapsed row never
 * grows taller than the legacy one-liner.
 */
function ToolRowShell({
  row,
  live,
  detail,
  expandable,
  sameWindow,
}: {
  row: ChatToolRow;
  live: boolean;
  detail: string;
  expandable: boolean;
  sameWindow: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid={`tool-row-${row.callID}`} data-tool-status={row.status}>
      <div className="flex min-w-0 items-center gap-1.5 rounded-lg border border-border/50 bg-muted/30 px-2.5 py-1 text-xs text-muted-foreground">
        <ToolStatusIcon status={row.status} live={live} />
        <span className="font-medium text-foreground/80">{toolVerb(row.tool)}</span>
        {detail ? (
          <span className="truncate font-mono text-[11px]" title={detail}>
            {detail}
          </span>
        ) : null}
        {sameWindow ? (
          <span
            data-testid={`same-window-${row.callID}`}
            className="shrink-0 rounded bg-amber-500/15 px-1 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300"
            title="Same file + window as a prior read this turn"
          >
            same window
          </span>
        ) : null}
        {expandable ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={open ? "Hide tool detail" : "Show tool detail"}
            data-testid={`tool-expand-${row.callID}`}
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex shrink-0 items-center rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <ChevronDown
              size={13}
              className={"transition-transform " + (open ? "rotate-180" : "")}
            />
          </button>
        ) : null}
      </div>
      {expandable && open ? <ToolRowDetail row={row} /> : null}
    </div>
  );
}

/** The collapsible enriched-detail panel (rendered only when open). */
function ToolRowDetail({ row }: { row: ChatToolRow }) {
  const d = row.detail;
  if (!d) return null;
  return (
    <div
      data-testid={`tool-detail-${row.callID}`}
      className="mt-1 space-y-1.5 rounded-lg border border-border/50 bg-muted/20 px-2.5 py-2 text-[11px] leading-relaxed text-muted-foreground"
    >
      {/* Bash: the 2K inline excerpt (F2) lives only in the chat
          expander — the detail surface already shows the full output
          via the BashTool card. */}
      {d.output_excerpt ? (
        <pre
          data-testid={`bash-output-${row.callID}`}
          className="max-h-40 overflow-auto rounded bg-background/60 p-1.5 font-mono text-[11px] whitespace-pre-wrap break-words"
        >
          {d.output_excerpt}
        </pre>
      ) : null}
      <ToolDetailMeta tool={row.tool} detail={d} className="space-y-1.5" />
    </div>
  );
}

function ToolStatusIcon({ status, live }: { status: string; live: boolean }) {
  if (status === "completed") {
    return <Check size={12} className="shrink-0 text-emerald-600 dark:text-emerald-400" />;
  }
  if (status === "error" || status === "failed") {
    return <AlertTriangle size={12} className="shrink-0 text-rose-600 dark:text-rose-400" />;
  }
  if (live || status === "running") {
    return <Loader2 size={12} className="shrink-0 animate-spin text-amber-600 dark:text-amber-400" />;
  }
  return <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/50" />;
}

/** Edit-like rows (persisted input carries old/new strings) get the diff card. */
function isEditRow(t: ChatToolRow): boolean {
  if (!/edit|write|create|file|patch/i.test(t.tool ?? "")) return false;
  const input = t.input ?? {};
  return (
    "filePath" in input ||
    "file_path" in input ||
    "oldString" in input ||
    "old_string" in input ||
    "newString" in input ||
    "new_string" in input ||
    "path" in input
  );
}

/** Map a compact chat row to the AI-SDK-style `part` shape EditTool expects
 *  (same mapping as the delegation DetailView's edit rows). */
function toToolEditPart(t: ChatToolRow): Record<string, unknown> {
  const state =
    t.status === "completed"
      ? "output-available"
      : t.status === "running" || t.status === "pending"
        ? "input-streaming"
        : "call";
  const isWrite = /write|create/i.test(t.tool ?? "");
  return {
    id: t.callID,
    toolCallId: t.callID,
    type: isWrite ? "tool-write" : "tool-edit",
    state,
    input: t.input ?? {},
    output: null,
    result: null,
  };
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
        className="mb-2.5 overflow-hidden rounded-xl border border-primary/25 bg-gradient-to-b from-primary/[0.08] to-transparent"
      >
        <div className="flex items-center gap-1.5 px-3 pt-2 text-[11px] font-semibold text-primary">
          <Brain size={12} className="animate-pulse" />
          <span className="animate-shimmer-text">Reasoning…</span>
          <span className="typing-dots ml-1" aria-hidden>
            <span />
            <span />
            <span />
          </span>
        </div>
        <div className="max-h-40 overflow-y-auto whitespace-pre-wrap px-3 pb-2.5 pt-1 text-xs leading-relaxed text-muted-foreground scrollbar-thin">
          {thinking}
        </div>
      </div>
    );
  }
  return (
    <details
      data-testid="thinking-block"
      data-state="done"
      className="group/think mb-2.5 rounded-xl border border-border/60 bg-muted/30 px-3 py-2 transition-colors hover:border-primary/30"
    >
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
        <Brain size={12} className="text-primary" />
        <span>Reasoning trace</span>
        <span className="ml-auto text-[10px] text-muted-foreground/60 group-open/think:hidden">
          expand
        </span>
      </summary>
      <div className="mt-1.5 max-h-60 overflow-y-auto whitespace-pre-wrap border-t border-border/40 pt-1.5 text-xs leading-relaxed text-muted-foreground scrollbar-thin">
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
        className="h-7 w-7 rounded-lg transition-colors hover:bg-primary/10 hover:text-primary"
        onClick={() => void retry()}
      >
        <RotateCcw size={13} />
      </TooltipIconButton>
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton
          tooltip={isCopied ? "Copied" : "Copy"}
          variant="ghost"
          size="sm"
          className="h-7 w-7 rounded-lg transition-colors hover:bg-primary/10 hover:text-primary"
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
  return <span className="whitespace-pre-wrap break-words">{value}</span>;
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
        className="w-full resize-y rounded-2xl border border-ring bg-card px-3.5 py-2.5 text-sm text-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-ring/40"
      />
      <div className="mt-1.5 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={() => setEditing(false)}
          className="rounded-lg px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={saveEdit}
          data-testid="user-message-edit-save"
          className="rounded-lg bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground shadow-sm transition-all hover:bg-primary/90"
        >
          Save &amp; resend
        </button>
      </div>
    </div>
  ) : (
    <div className="rounded-2xl rounded-br-md bg-gradient-to-br from-primary to-primary/75 px-4 py-2.5 text-sm leading-relaxed text-primary-foreground shadow-md shadow-primary/20 transition-shadow group-hover/message:shadow-lg group-hover/message:shadow-primary/25">
      <MessagePrimitive.Parts components={{ Text: UserPlainText }} />
    </div>
  );

  return (
    <div
      className="animate-message-in group/message flex justify-end"
      data-testid="user-message-row"
      data-message-id={messageId}
    >
      <div className="flex max-w-[78%] items-end gap-2">
        <div className="flex min-w-0 flex-col items-end gap-1">
          {custom.superseded ? (
            <SupersededBlock label="Superseded" preview={threadTextOf(message)}>
              {bubble}
            </SupersededBlock>
          ) : (
            bubble
          )}
          <div className="flex items-center gap-1 pr-1">
            {time && (
              <time className="text-[11px] tabular-nums text-muted-foreground/70">{time}</time>
            )}
            {!isRunning && !editing && actions && (
              <button
                type="button"
                onClick={startEdit}
                title="Edit and resend"
                aria-label="Edit and resend"
                data-testid="user-message-edit"
                className="rounded-md p-1 text-muted-foreground/60 opacity-0 transition-all hover:bg-muted hover:text-foreground focus:opacity-100 group-hover/message:opacity-100"
              >
                <Pencil size={12} />
              </button>
            )}
          </div>
        </div>
        <Avatar
          size="sm"
          className="shrink-0 border border-border/60 bg-gradient-to-br from-muted to-muted/50"
          fallback={<User size={15} className="text-muted-foreground" />}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composer (Enter sends / Shift+Enter newline via ComposerPrimitive;
// the stop affordance POSTs the turn-cancel endpoint: the server
// stops the whole subtree and persists the partial reply as a
// `cancelled` bubble, so stopping never loses the thread).
// ---------------------------------------------------------------------------

function Composer() {
  const isEmpty = useAuiState((s) => s.composer.isEmpty);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const actions = useChatActions();

  return (
    <div className="bg-gradient-to-t from-background via-background/95 to-transparent px-4 pb-4 pt-2">
      <ComposerPrimitive.Root
        className="mx-auto flex w-full max-w-3xl items-end gap-2 rounded-2xl border border-border/70 bg-card/95 p-2 pl-3.5 shadow-xl shadow-black/5 backdrop-blur transition-all focus-within:border-primary/60 focus-within:shadow-primary/10 focus-within:ring-2 focus-within:ring-ring/25"
        data-testid="chat-composer"
      >
        <ComposerPrimitive.Input
          placeholder={isRunning ? "The orchestrator is replying…" : "Ask the swarm anything…"}
          autoFocus
          rows={1}
          data-testid="chat-composer-input"
          className="max-h-40 min-h-[38px] flex-1 resize-none bg-transparent py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none"
        />
        {isRunning ? (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label="Stop the turn"
                  data-testid="chat-composer-stop"
                  onClick={() => actions?.cancel()}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-destructive to-destructive/70 text-destructive-foreground shadow-md shadow-destructive/25 transition-all hover:-translate-y-px hover:shadow-lg hover:shadow-destructive/30 active:translate-y-0"
                >
                  <OctagonX size={16} />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top">Stop the turn (keeps the partial reply)</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : (
          <ComposerPrimitive.Send asChild>
            <button
              type="button"
              aria-label="Send message"
              data-testid="chat-composer-send"
              className={cn(
                "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-primary to-primary/70 text-primary-foreground shadow-md shadow-primary/25 transition-all",
                "hover:-translate-y-px hover:shadow-lg hover:shadow-primary/30 active:translate-y-0",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                "disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none",
              )}
              disabled={isEmpty}
            >
              <ArrowUp size={16} strokeWidth={2.5} />
            </button>
          </ComposerPrimitive.Send>
        )}
      </ComposerPrimitive.Root>
      <p className="mx-auto mt-1.5 flex w-full max-w-3xl items-center justify-center gap-1.5 text-center text-[10px] text-muted-foreground/60">
        <span className="kbd">Enter</span>
        <span>to send</span>
        <span aria-hidden>·</span>
        <span className="kbd">Shift</span>
        <span>+</span>
        <span className="kbd">Enter</span>
        <span>for a newline</span>
      </p>
    </div>
  );
}
