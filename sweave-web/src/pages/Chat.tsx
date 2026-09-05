/**
 * Chat page placeholder (M1.9 Step 1).
 *
 * Step 1 ships the shell; Step 2 fills the streaming message list,
 * the session picker, and the message composer. The placeholder
 * is just enough to render the page so the route is reachable.
 */
import { useApp } from "@/context/AppProvider";

export function ChatPage() {
  const { activeProject, activeSession } = useApp();
  return (
    <div className="p-6 space-y-4" data-testid="chat-page">
      <header>
        <h1 className="text-xl font-semibold">Chat</h1>
        <p className="text-sm text-muted-foreground">
          {activeProject
            ? activeSession
              ? `Session: ${activeSession.name}`
              : "Select a session in the sidebar to start a conversation."
            : "Activate a project to start a conversation."}
        </p>
      </header>
      <div
        data-testid="chat-placeholder"
        className="border border-dashed border-border rounded p-8 text-center text-sm text-muted-foreground"
      >
        Chat surface lands in Step 2 (streaming + session picker).
      </div>
    </div>
  );
}
