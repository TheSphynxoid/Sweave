/**
 * Sweave chat runtime hook (R4.2 step 1).
 *
 * Bridges assistant-ui's `useExternalStoreRuntime` to our backend
 * contract. Our `SweaveThreadState` (see `./runtime`) is the source
 * of truth; `projectThread` is the projection assistant-ui renders.
 * WS events mutate the state in place; the backend stays
 * authoritative (`Session.messages`).
 *
 * State flow per user turn:
 *   1. `onNew` (composer submit) -> optimistic user append + POST
 *      `/api/sessions/{id}/messages`. Turns are serial: while a turn
 *      is in flight (`turn !== "idle"`), `isSendDisabled` blocks send.
 *   2. WS `message.added` (user)   -> reconcile the optimistic copy.
 *   3. WS `delegation.status_changed` (running) -> turn running.
 *   4. WS `chat.thinking` × N      -> append to the bubble's Thinking block.
 *   5. WS `chat.delta` × N        -> append to the streaming bubble.
 *   6. WS `message.added` (assistant) -> finalize + turn idle.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  useExternalStoreRuntime,
  type AppendMessage,
} from "@assistant-ui/react";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import type { SessionMessage } from "@/types";
import {
  applyDelta,
  applyMessageAdded,
  applyRerun,
  applyStatusChanged,
  applySubmit,
  applyThinking,
  initialThreadState,
  mergeHistory,
  projectThread,
  stateFromHistory,
  type SweaveThreadState,
} from "./runtime";

/** Extract the concatenated text of an assistant-ui append message. */
export function extractAppendText(message: AppendMessage): string {
  return message.content
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");
}

export function useSweaveChatRuntime(sessionId: string | null) {
  const { subscribe } = useWS();
  const [state, setState] = useState<SweaveThreadState>(initialThreadState);
  // Which session the local state belongs to. A session switch
  // replaces (never merges): merging would leak the old session's
  // in-flight bubble into the new thread.
  const stateSessionRef = useRef<string | null>(null);

  // History load: on session switch, replace the state with the REST
  // history (authoritative).
  const { data: sessionDetail } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => (sessionId ? api.getSession(sessionId) : Promise.resolve(null)),
    enabled: !!sessionId,
  });

  useEffect(() => {
    if (sessionDetail) {
      // Guard against a stale query for the previous session (key
      // change refetches async): only fold history that belongs to
      // the active session, and merge so a mid-turn refetch never
      // wipes the optimistic/streaming entries.
      if (sessionDetail.id !== sessionId) return;
      if (stateSessionRef.current !== sessionId) {
        stateSessionRef.current = sessionId;
        setState(stateFromHistory(sessionDetail.messages));
        return;
      }
      const messages = sessionDetail.messages;
      setState((prev) =>
        prev.entries.length === 0 && prev.turn === "idle"
          ? stateFromHistory(messages)
          : mergeHistory(prev, messages),
      );
    } else if (sessionId === null) {
      stateSessionRef.current = null;
      setState(initialThreadState());
    }
  }, [sessionDetail, sessionId]);

  // WS subscriptions, scoped to the active session. `sessionId` is in
  // the dep list but the closures read the current value directly.
  useEffect(() => {
    if (!sessionId) return;
    const offDelta = subscribe("chat.delta", (env) => {
      const data = env.data as Record<string, unknown>;
      if (data.session_id !== sessionId) return;
      if (typeof data.delegation_id !== "string" || typeof data.text !== "string") return;
      setState((s) => applyDelta(s, data.delegation_id as string, data.text as string));
    });
    const offThinking = subscribe("chat.thinking", (env) => {
      const data = env.data as Record<string, unknown>;
      if (data.session_id !== sessionId) return;
      if (typeof data.delegation_id !== "string" || typeof data.text !== "string") return;
      setState((s) => applyThinking(s, data.delegation_id as string, data.text as string));
    });
    const offAdded = subscribe("message.added", (env) => {
      const data = env.data as Record<string, unknown>;
      if (data.session_id !== sessionId) return;
      const message = data.message as SessionMessage | undefined;
      if (!message) return;
      setState((s) => applyMessageAdded(s, message));
    });
    const offStatus = subscribe("delegation.status_changed", (env) => {
      const data = env.data as Record<string, unknown>;
      if (data.session_id !== sessionId && data.session_id !== undefined) return;
      const delegationId = data.delegation_id;
      const status = data.status;
      if (typeof delegationId !== "string" || typeof status !== "string") return;
      setState((s) => applyStatusChanged(s, delegationId, status));
    });
    return () => {
      offDelta();
      offThinking();
      offAdded();
      offStatus();
    };
  }, [sessionId, subscribe]);

  // Projection -> assistant-ui runtime.
  const messages = useMemo(() => projectThread(state), [state]);
  const isRunning = state.turn === "running" || state.turn === "queued";

  const rerun = useCallback(
    async (messageId: string, content?: string) => {
      if (!sessionId) return;
      // Optimistic supersede first (referential check: applyRerun
      // returns the same object when the target is missing, and we
      // must not POST a turn the thread doesn't show).
      let applied = false;
      setState((s) => {
        const next = applyRerun(s, messageId, content);
        applied = next !== s;
        return next;
      });
      if (!applied) return;
      // The backend runs the turn; the WS events are authoritative
      // for the thread view (the response's `assistant` is ignored).
      try {
        await api.rerunTurn(
          sessionId,
          content === undefined
            ? { from_message_id: messageId }
            : { from_message_id: messageId, content },
        );
      } catch {
        // The backend persists an error assistant message on
        // failure; the WS `message.added` surfaces it. A transport
        // failure self-heals on the next history refetch
        // (mergeHistory drops non-authoritative rows).
      }
    },
    [sessionId],
  );

  const onNew = useCallback(
    async (message: AppendMessage) => {
      if (!sessionId) return;
      const text = extractAppendText(message);
      if (!text.trim()) return;
      // Guard against double-submit: only applySubmit if turn is idle.
      // The UI's isSendDisabled should prevent this, but React state
      // updates are async so a rapid double-click/keypress can race.
      setState((s) => {
        if (s.turn !== "idle") return s;
        return applySubmit(s, text);
      });
      // The backend runs the turn; the WS events are authoritative
      // for the thread view (the response's `assistant` is ignored).
      try {
        await api.sendMessage(sessionId, { role: "user", content: text });
      } catch {
        // The backend persists an error assistant message on
        // failure; the WS `message.added` surfaces it.
      }
    },
    [sessionId],
  );

  return {
    runtime: useExternalStoreRuntime({
      messages,
      isRunning,
      isSendDisabled: isRunning,
      convertMessage: (m) => m,
      onNew,
    }),
    rerun,
  };
}