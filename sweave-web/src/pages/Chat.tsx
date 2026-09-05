/**
 * Chat page (M1.9 Step 2).
 *
 * The input funnel. Patches a single streaming bubble per
 * delegation id on ``chat.delta`` events; replaces the bubble
 * with the persisted message on ``message.added`` (the
 * M1.8 no-rerender invariant; React + the reducer work
 * together to keep the patch local). The serial-turn
 * indicator is the composer's "thinking" state.
 *
 * Initial messages: the session detail (lazy fetch via
 * React Query). When the session is switched, the chat state
 * resets to a fresh state + a new fetch.
 */
import { useEffect, useMemo, useReducer, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { useWS } from "@/context/WSProvider";
import { applyChatEvent, initialChatState } from "./chat/reducer";
import { Composer } from "./chat/Composer";
import { SessionPicker } from "./chat/SessionPicker";
import { MessageList } from "./chat/MessageList";
import { cn } from "@/utils/cn";
import type { SessionMessage } from "@/types";

export function ChatPage() {
  const { activeProject, activeSession } = useApp();
  const { subscribe } = useWS();
  const qc = useQueryClient();
  const [chat, dispatch] = useReducer(applyChatEvent, undefined, initialChatState);

  // Keep the last-seen session id in a ref so the WS subscriber
  // closure can filter events by session without re-subscribing
  // on every render.
  const sessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    sessionIdRef.current = activeSession?.id ?? null;
  }, [activeSession?.id]);

  // Fetch the session's messages + apply the persisted ones to
  // the chat state on session change. The persisted state is the
  // ground truth; deltas are an overlay.
  const sessionId = activeSession?.id ?? null;
  const { data: sessionDetail } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => (sessionId ? api.getSession(sessionId) : Promise.resolve(null)),
    enabled: !!sessionId,
  });

  // Reset the chat state on session change + seed with the
  // persisted messages from the fetch. The reducer is a pure
  // function, so we dispatch in order.
  useEffect(() => {
    if (!sessionDetail) return;
    // Build a clean state; dispatching a turnBoundary clears
    // any orphan streaming bubbles from the previous session.
    dispatch({ kind: "turnBoundary", sessionId: sessionDetail.id });
    for (const m of sessionDetail.messages) {
      dispatch({ kind: "messageAdded", sessionId: sessionDetail.id, message: m });
    }
  }, [sessionDetail?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Subscribe to WS events. Two subscriptions:
  //   1. chat.delta       -> patch the streaming bubble.
  //   2. message.added     -> append the persisted message.
  //   Both are scoped to the active session; cross-session
  //   events are filtered by the closure.
  useEffect(() => {
    if (!sessionId) return;
    const offDelta = subscribe("chat.delta", (env) => {
      const data = env.data as { session_id?: string; delegation_id?: string; text?: string };
      if (data.session_id !== sessionId) return;
      if (typeof data.delegation_id !== "string" || typeof data.text !== "string") return;
      dispatch({
        kind: "delta",
        sessionId: sessionId,
        delegationId: data.delegation_id,
        text: data.text,
      });
    });
    const offAdded = subscribe("message.added", (env) => {
      const data = env.data as { session_id?: string; message?: SessionMessage };
      if (data.session_id !== sessionId) return;
      if (!data.message) return;
      dispatch({
        kind: "messageAdded",
        sessionId: sessionId,
        message: data.message,
      });
      // Refresh the session detail so the persisted view stays
      // in sync (the message we just got is the new persisted
      // one; the local cache would otherwise lag).
      void qc.invalidateQueries({ queryKey: ["session", sessionId] });
    });
    return () => {
      offDelta();
      offAdded();
    };
  }, [sessionId, subscribe, qc]);

  // The "current turn" indicator: if any delegation is
  // streaming, the orchestrator is mid-turn. Used to disable
  // the composer (per the M1.7 serial-turn ruling; the chat
  // loop is per-session).
  const isStreaming = useMemo(
    () => Object.keys(chat.streamingByDelegation).length > 0,
    [chat.streamingByDelegation],
  );

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
          {isStreaming && (
            <span
              data-testid="chat-busy"
              className="text-xs text-muted-foreground flex items-center gap-1"
            >
              <span className={cn("w-2 h-2 rounded-full bg-amber-500")} />
              orchestrator is replying...
            </span>
          )}
        </div>
      </div>
      <MessageList
        messages={chat.messages}
        streaming={chat.streamingByDelegation}
      />
      <Composer
        sessionId={activeSession.id}
        disabled={isStreaming}
        onUserMessage={(content) => {
          // Optimistic local push: the user sees their own
          // message immediately. The server-persisted version
          // arrives via WS message.added; the dispatcher's
          // dedupe is via message id (the server-assigned id
          // matches; we use a synthetic id here so the
          // reducer's append-dedupe doesn't merge them by
          // accident). For now: we trust the WS event to
          // append the persisted version; the optimistic push
          // gives instant feedback.
          const synthetic: SessionMessage = {
            id: `local-${Date.now()}`,
            role: "user",
            content,
            timestamp: new Date().toISOString(),
            agent: null,
            tool_name: null,
            tool_result: null,
            metadata: {},
          };
          dispatch({ kind: "messageAdded", sessionId: activeSession.id, message: synthetic });
        }}
        onAssistantMessage={(content, delegationId) => {
          // The server returns the assistant message in the
          // sendMessage response; we push a local version keyed
          // by the delegation_id. The matching chat.delta +
          // message.added that follow the response will
          // replace this local bubble (or the persisted msg
          // matches by id and we no-op). For now: simply push
          // the bubble keyed to the delegation.
          if (!delegationId) return;
          dispatch({
            kind: "delta",
            sessionId: activeSession.id,
            delegationId,
            text: content,
          });
        }}
      />
    </div>
  );
}
