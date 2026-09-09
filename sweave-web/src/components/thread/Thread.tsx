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
import { useEffect, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Bot,
  Check,
  Copy,
  OctagonX,
  Sparkles,
  User,
} from "lucide-react";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { Avatar } from "@/components/assistant-ui/elements/avatar";
import { Skeleton } from "@/components/assistant-ui/elements/skeleton";
import { useWS } from "@/context/WSProvider";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { AssistantTextPart } from "./markdown/AssistantTextPart";
import { TurnDelegations } from "./TurnDelegations";
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
  if (!isRunning) return null;

  const last = messages.length ? messages[messages.length - 1] : undefined;
  const lastStreaming =
    last !== undefined &&
    last.role === "assistant" &&
    (last as { status?: { type?: string } }).status?.type === "running";
  const chars = lastStreaming ? threadTextOf(last).length : 0;
  const delegationId = last !== undefined ? threadCustomOf(last).delegationId : null;
  const wsDown = wsState !== "open";

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
        {delegationId && (
          <span
            className="rounded border border-border bg-muted px-1.5 py-px font-mono text-[10px]"
            title={`Chat turn delegation ${delegationId}`}
          >
            {delegationId.slice(0, 8)}
          </span>
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
  const messageId = useAuiState((s) => s.message.id);
  const isRunning = useAuiState((s) => s.message.status?.type === "running");
  const time = formatTimestamp(custom.timestamp);

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
            <span
              className="rounded border border-border bg-muted px-1.5 py-px font-mono text-[10px] text-muted-foreground"
              title={`Chat turn delegation ${custom.delegationId}`}
            >
              {custom.delegationId.slice(0, 8)}
            </span>
          )}
        </div>

        <div className="text-sm leading-relaxed">
          <MessagePrimitive.Parts components={{ Text: AssistantTextPart }} />
          {isRunning && <span className="streaming-cursor" aria-hidden />}
        </div>

        {custom.delegationId && <TurnDelegations parentDelegationId={custom.delegationId} />}

        <div className="mt-1.5 flex items-center justify-between gap-2">
          {time && <time className="text-[11px] text-muted-foreground/70">{time}</time>}
          <div className="flex-1" />
          <AssistantActionBar />
        </div>
      </div>
    </div>
  );
}

/**
 * Copy + timestamp only (ruling 3: no disabled fake buttons; R4.3 adds the rest).
 * The Root unmounts itself on non-last messages and while the run is in
 * flight (hideWhenRunning + autohide="not-last"), so no hover CSS is needed.
 */
function AssistantActionBar() {
  const isCopied = useAuiState((s) => s.message.isCopied);
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="flex items-center gap-0.5"
      data-testid="action-bar"
    >
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
  const messageId = useAuiState((s) => s.message.id);
  const time = formatTimestamp(custom.timestamp);

  return (
    <div
      className="flex justify-end"
      data-testid="user-message-row"
      data-message-id={messageId}
    >
      <div className="flex max-w-[75%] items-end gap-2">
        <div className="flex min-w-0 flex-col items-end gap-1">
          <div className="rounded-2xl border border-primary/20 bg-primary/10 px-3.5 py-2 text-sm shadow-sm transition-shadow group-hover/message:shadow-md">
            <MessagePrimitive.Parts components={{ Text: UserPlainText }} />
          </div>
          {time && <time className="pr-1 text-[11px] text-muted-foreground/70">{time}</time>}
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
        className="mx-auto flex w-full max-w-3xl items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-lg focus-within:border-ring/60"
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
