/**
 * Chat page (M1.9 Step 2, R4.2 Step 1).
 *
 * The input funnel. R4.2 replaces the hand-rolled state machine
 * (MessageList + reducer) with assistant-ui's `Thread` driven by
 * `useSweaveChatRuntime` (see `@/lib/chat/useSweaveChatRuntime`).
 * The backend contract is unchanged; the runtime is a view projection
 * of our REST history + WS events.
 *
 * Session switching stays in the R4.1 tree (SessionPicker here is the
 * in-thread jump affordance); create/switch/rename-in-thread lands in
 * R4.2 step 3.
 */
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useApp } from "@/context/AppProvider";
import { useSweaveChatRuntime } from "@/lib/chat/useSweaveChatRuntime";
import { SessionPicker } from "./chat/SessionPicker";
import { Thread } from "@/components/thread/Thread";

export function ChatPage() {
  const { activeProject, activeSession } = useApp();
  const runtime = useSweaveChatRuntime(activeSession?.id ?? null);

  if (!activeProject) {
    return (
      <div className="p-6" data-testid="chat-page">
        <p className="text-sm text-muted-foreground">
          Activate a project to start a conversation.
        </p>
      </div>
    );
  }

  if (!activeSession) {
    return (
      <div className="p-6 space-y-3" data-testid="chat-page">
        <p className="text-sm text-muted-foreground">
          Select a session in the topbar to start a conversation.
        </p>
        <SessionPicker />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full" data-testid="chat-page">
      <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <SessionPicker />
        </div>
      </div>
      <AssistantRuntimeProvider runtime={runtime}>
        <Thread />
      </AssistantRuntimeProvider>
    </div>
  );
}