/**
 * Chat-level actions beyond append (edit + resend / retry).
 *
 * `useSweaveChatRuntime` provides these alongside the assistant-ui
 * runtime; `Thread` consumes them via context so message components
 * stay decoupled from the hook. Null outside a live chat session
 * (e.g. the dev lab) — consumers must guard.
 */
import { createContext, useContext } from "react";

export interface ChatActions {
  /**
   * Re-run the turn starting at a user message. `content` set =
   * edit + resend; omitted = retry the same text. Optimistic
   * (supersede flags + content swap apply instantly); the WS events
   * for the new turn drive the rest.
   */
  rerun: (messageId: string, content?: string) => void;
}

export const ChatActionsContext = createContext<ChatActions | null>(null);

export function useChatActions(): ChatActions | null {
  return useContext(ChatActionsContext);
}
