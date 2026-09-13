/**
 * Chat page (M1.9 Step 2, R4.2 Step 1 + step 2-pre).
 *
 * The input funnel. R4.2 replaces the hand-rolled state machine
 * with assistant-ui's `Thread` driven by `useSweaveChatRuntime`
 * (see `@/lib/chat/useSweaveChatRuntime`). The backend contract is
 * unchanged; the runtime is a view projection of our REST history +
 * WS events.
 *
 * The runtime is created HERE and ONLY here: the Thread consumes the
 * provided runtime via assistant-ui's context (the previous double
 * `useSweaveChatRuntime` — one in Chat.tsx, one inside Thread.tsx —
 * ran two WS subscriptions and two histories for the same session).
 *
 * Session switching stays in the R4.1 tree (SessionPicker here is the
 * in-thread jump affordance); create/switch/rename-in-thread lands in
 * R4.2 step 3.
 */
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { MessagesSquare, Sparkles } from "lucide-react";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { useUIStore } from "@/store/ui";
import { useSweaveChatRuntime } from "@/lib/chat/useSweaveChatRuntime";
import { ChatActionsContext } from "@/lib/chat/actions";
import { SessionPicker } from "./chat/SessionPicker";
import { Thread } from "@/components/thread/Thread";
import { Button } from "@/components/ui/button";
import { TextShimmer } from "@/components/agent-elements/text-shimmer";
import { cn } from "@/utils/cn";

export function ChatPage() {
  const { activeProject, activeSession } = useApp();
  const setCreateOpen = useUIStore((s) => s.setCreateProjectOpen);
  const { runtime, rerun } = useSweaveChatRuntime(activeSession?.id ?? null);

  if (!activeProject) {
    return (
      <div className="chat-hero-orb flex h-full items-center justify-center p-6" data-testid="chat-page">
        <div className="animate-message-in max-w-sm space-y-4 text-center">
          <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-primary via-primary/70 to-primary/30 text-primary-foreground shadow-lg shadow-primary/25">
            <MessagesSquare size={22} />
          </div>
          <div className="space-y-1">
            <TextShimmer className="text-sm font-medium">Sweave orchestrator</TextShimmer>
            <p className="text-sm text-muted-foreground">
              No project is active. Create or select a project to start a conversation.
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)} className="shadow-md shadow-primary/20">
            Create a project
          </Button>
        </div>
      </div>
    );
  }

  if (!activeSession) {
    return (
      <div className="chat-hero-orb flex h-full items-center justify-center p-6" data-testid="chat-page">
        <div className="animate-message-in max-w-sm space-y-3 text-center">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl border border-border/60 bg-card/80 text-primary shadow-sm backdrop-blur">
            <Sparkles size={20} />
          </div>
          <p className="text-sm text-muted-foreground">
            Select a session in the topbar to start a conversation.
          </p>
          <SessionPicker />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col" data-testid="chat-page">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/60 bg-card/50 px-4 py-2 backdrop-blur">
        <div className="flex min-w-0 items-center gap-2">
          <span className="hidden items-center gap-1.5 rounded-full border border-primary/25 bg-primary/10 px-2.5 py-1 text-[11px] font-semibold text-primary sm:flex">
            <Sparkles size={11} />
            Orchestrator
          </span>
          <SessionPicker />
        </div>
        <WsDot />
      </div>
      <div className="min-h-0 flex-1">
        <AssistantRuntimeProvider runtime={runtime}>
          <ChatActionsContext.Provider value={{ rerun }}>
            <Thread />
          </ChatActionsContext.Provider>
        </AssistantRuntimeProvider>
      </div>
    </div>
  );
}

/**
 * Connection dot for the chat header. Idle turns load history over
 * REST, but deltas only flow over the socket — a visibly-down socket
 * explains a turn that looks stuck before its first token.
 */
function WsDot() {
  const { state } = useWS();
  const open = state === "open";
  return (
    <span
      data-testid="chat-ws-dot"
      data-ws-state={state}
      title={open ? "Live updates connected" : `Live updates ${state}`}
      className={cn(
        "flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
        open
          ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          open ? "bg-emerald-500" : "bg-amber-500 animate-pulse",
        )}
      />
      {open ? "live" : state}
    </span>
  );
}
