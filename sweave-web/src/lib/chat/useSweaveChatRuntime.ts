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
import type { SessionMessage, TurnSnapshot } from "@/types";
import axios from "axios";
import {
  applyDelta,
  applyMessageAdded,
  applyRerun,
  applyStatusChanged,
  applySubmit,
  applyThinking,
  applyTurnSnapshot,
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

/**
 * Pull the ACTIVE-turn snapshot out of a turn-send error, when the
 * error IS the double-send guard (HTTP 409 whose body carries the
 * snapshot). Everything else (network, 500, 404) yields ``null`` --
 * callers fall through to the legacy silent-catch path.
 */
export function turnSnapshotFromSendError(err: unknown): TurnSnapshot | null {
  if (!axios.isAxiosError(err)) return null;
  const status = err.response?.status;
  const data = err.response?.data as { detail?: { turn?: TurnSnapshot } } | undefined;
  const turn = data?.detail?.turn;
  if (status !== 409 || !turn) return null;
  return turn;
}

export function useSweaveChatRuntime(sessionId: string | null) {
  // The connection state doubles as the refresh/reconnect recovery
  // trigger: every transition to "open" (page load after the socket
  // comes up, or a reconnect) re-eyes the server's active-turn
  // snapshot for the current session (2026-09-10 recovery contract).
  const { state: wsState, subscribe } = useWS();
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

  // Active-turn recovery (2026-09-10 contract): on page load (the
  // first socket "open" after mount), on session activation, and on
  // every WS reconnect, ask the server whether a turn is still
  // running for this session. If so, restore the waiting/streaming
  // indicator + the accumulated partial text; the turn's completion
  // event via WS (authoritative `message.added`) clears it. In-memory
  // only: after a server restart the answer is always "no active
  // turn" and nothing is expressed. applyTurnSnapshot is defensive
  // against the live state already streaming for the same delegation.
  useEffect(() => {
    if (!sessionId || wsState !== "open" || !sessionDetail) return;
    if (sessionDetail.id !== sessionId) return;
    let cancelled = false;
    api.getActiveTurn(sessionId).then((snapshot) => {
      if (cancelled || !snapshot) return;
      // Stale-session guard: the snapshot must belong to the session
      // still active in this hook.
      if (snapshot.session_id !== sessionId) return;
      setState((s) => applyTurnSnapshot(s, snapshot));
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, wsState, sessionDetail]);

  // WS subscriptions, scoped to the active session. `sessionId` is in
  // the dep list but the closures read the current value directly.
  useEffect(() => {
    if (!sessionId) return;
    const offDelta = subscribe("chat.delta", (env) => {
      const data = env.data as Record<string, unknown>;
      if (data.session_id !== sessionId) return;
      if (typeof data.delegation_id !== "string" || typeof data.text !== "string") return;
      const round = typeof data.round === "number" && data.round >= 0 ? Math.floor(data.round) : 0;
      setState((s) => applyDelta(s, data.delegation_id as string, data.text as string, round));
    });
    const offThinking = subscribe("chat.thinking", (env) => {
      const data = env.data as Record<string, unknown>;
      if (data.session_id !== sessionId) return;
      if (typeof data.delegation_id !== "string" || typeof data.text !== "string") return;
      const round = typeof data.round === "number" && data.round >= 0 ? Math.floor(data.round) : 0;
      setState((s) => applyThinking(s, data.delegation_id as string, data.text as string, round));
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
      try {
        await api.rerunTurn(
          sessionId,
          content === undefined
            ? { from_message_id: messageId }
            : { from_message_id: messageId, content },
        );
      } catch (err: unknown) {
        // Double-send guard (409 + snapshot): a turn was already
        // running for this session -- adopt the snapshot instead of
        // erroring. Other failures: the backend persists an error
        // assistant message; the WS `message.added` surfaces it.
        const snapshot = turnSnapshotFromSendError(err);
        if (snapshot && snapshot.session_id === sessionId) {
          setState((s) => applyTurnSnapshot(s, snapshot));
        }
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
      // The optimistic user message stays: the running turn's own
      // `message.added` (user) reconciles it against the persisted copy.
      try {
        await api.sendMessage(sessionId, { role: "user", content: text });
      } catch (err: unknown) {
        // Double-send guard (HTTP 409 whose body carries the active
        // turn's snapshot): adopt the snapshot as if the turn were
        // locally started -- switch to the waiting/streaming view,
        // block the composer, resume via WS events. NO error path:
        // the user's message simply belongs to a turn that already
        // started. Everything else (network, 500, 404): the backend
        // persists an error assistant message; WS surfaces it.
        const snapshot = turnSnapshotFromSendError(err);
        if (snapshot && snapshot.session_id === sessionId) {
          setState((s) => applyTurnSnapshot(s, snapshot));
        }
      }
    },
    [sessionId],
  );

  // isRunning is the server-truth-derived composer gate: it reads the
  // restored turn (snapshot) exactly like a locally-started one, so a
  // refresh can never resurrect an editable composer while a turn
  // runs. isSendDisabled below is the SAME invariant.
  const isRunning = state.turn === "running" || state.turn === "queued";

  const cancel = useCallback(() => {
    if (!sessionId) return;
    // Fire-and-forget: the server's `message.added` (cancelled
    // bubble) + `delegation.status_changed` settle the thread back
    // to idle. A 404 (turn already settled between render and tap)
    // is not worth surfacing.
    api.cancelTurn(sessionId).catch(() => {});
  }, [sessionId]);

  return {
    runtime: useExternalStoreRuntime({
      messages,
      isRunning,
      isSendDisabled: isRunning,
      convertMessage: (m) => m,
      onNew,
    }),
    rerun,
    onNew,
    cancel,
    /** Projected thread for tests/telemetry; the Thread renders `runtime`. */
    messages,
    /** Exposed for tests/telemetry only -- the Thread renders `runtime`. */
    turn: state.turn,
    isRunning,
  };
}
