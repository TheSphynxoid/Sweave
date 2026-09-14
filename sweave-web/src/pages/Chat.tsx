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
 * Session switching lives in the sidebar project/session tree (the
 * sole switching surface since the 2026-09-13 consolidation);
 * create/switch/rename-in-thread lands in R4.2 step 3.
 */
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { MessagesSquare, Sparkles } from "lucide-react";
import { useApp } from "@/context/AppProvider";
import { useUIStore } from "@/store/ui";
import { useSweaveChatRuntime } from "@/lib/chat/useSweaveChatRuntime";
import { ChatActionsContext } from "@/lib/chat/actions";
import { Thread } from "@/components/thread/Thread";
import { Button } from "@/components/ui/button";
import { TextShimmer } from "@/components/agent-elements/text-shimmer";

export function ChatPage() {
  const { activeProject, activeSession } = useApp();
  const setCreateOpen = useUIStore((s) => s.setCreateProjectOpen);
  const { runtime, rerun, cancel } = useSweaveChatRuntime(activeSession?.id ?? null);

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
            Select a session in the sidebar to start a conversation.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col" data-testid="chat-page">
      <div className="min-h-0 flex-1">
        <AssistantRuntimeProvider runtime={runtime}>
          <ChatActionsContext.Provider value={{ rerun, cancel }}>
            <Thread />
          </ChatActionsContext.Provider>
        </AssistantRuntimeProvider>
      </div>
    </div>
  );
}
