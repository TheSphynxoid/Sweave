/**
 * Sweave chat runtime adapter (R4.2 step 1 — pure state machine).
 *
 * DECISION (R4.2 step 0): `@assistant-ui/react-opencode` is REJECTED.
 * We adopt `useExternalStoreRuntime` (from `@assistant-ui/react`) with
 * a custom adapter over our REST + WS contract.
 *
 * The adapter is a VIEW PROJECTION:
 *   - REST history (`GET /api/sessions/{id}` → messages) is authoritative.
 *   - WS `chat.delta {session_id, delegation_id, text}` patches the
 *     in-flight assistant bubble (coalesced; M1.8 invariant: patch in
 *     place, never re-render the whole thread).
 *   - WS `message.added` finalizes the authoritative persisted message
 *     (user AND assistant messages both flow through here).
 *   - WS `delegation.status_changed` drives the serial-turn indicator
 *     (queued → running → done/failed).
 *
 * One-turn event order (from sweave/chat/loop.py `_run_turn_body` +
 * `_finalise_turn`), all scoped by `session_id`:
 *   1. `message.added` (user)      — the optimistic user message is
 *                                   confirmed by this persisted copy.
 *   2. `delegation.status_changed` (running)
 *   3. `chat.thinking` × N         — reasoning increments, keyed by
 *                                   `delegation_id` (only when the
 *                                   provider exposes reasoning parts).
 *   4. `chat.delta` × N            — streaming deltas, keyed by
 *                                   `delegation_id`.
 *   5. `delegation.status_changed` (done | failed)
 *   6. `message.added` (assistant) — `metadata.delegation_id` is the
 *                                   join key; `metadata.thinking`
 *                                   carries the full reasoning text;
 *                                   this replaces the bubble.
 *
 * This module is PURE (no React). The React hook in step 1b wraps it in
 * `useExternalStoreRuntime`. All functions are side-effect free and the
 * state machine is fully deterministic per event sequence -- the vitest
 * suite pins the ordering + finalize + queue semantics.
 */
import type { SessionMessage } from "@/types";
import type { ThreadMessageLike } from "@assistant-ui/react";

// ---------------------------------------------------------------------------
// State model
// ---------------------------------------------------------------------------

/** Serial-turn state per session. */
export type TurnState = "idle" | "queued" | "running";

/**
 * One entry in the adapter's message list. The list carries both
 * the authoritative persisted messages and the optimistic/streaming
 * ones; the projection flattens it into ``ThreadMessageLike[]``.
 */
export interface ChatEntry {
  message: SessionMessage;
  /** True for the in-flight assistant bubble (gets a "running" status in the thread). */
  streaming: boolean;
  /** Join key for streaming/finalize (chat turn delegation id). */
  delegationId?: string;
  /** True for a locally-optimistic user message (not yet confirmed by the server). */
  optimistic?: boolean;
  /** Live-accumulated reasoning text for the in-flight bubble (chat.thinking). */
  thinking?: string;
}

export interface SweaveThreadState {
  entries: ChatEntry[];
  turn: TurnState;
  activeDelegationId: string | null;
}

export function initialThreadState(): SweaveThreadState {
  return { entries: [], turn: "idle", activeDelegationId: null };
}

/** Fold REST history into the initial authoritative state. */
export function stateFromHistory(messages: SessionMessage[]): SweaveThreadState {
  return {
    entries: messages.map((message) => ({ message, streaming: false })),
    turn: "idle",
    activeDelegationId: null,
  };
}

/**
 * Merge a REST history reload into live state without dropping the
 * in-flight turn.
 *
 * A React Query refetch mid-turn (window refocus, reconnect) used to
 * replace the whole state via `stateFromHistory`, wiping the
 * optimistic user message + the streaming assistant bubble: the
 * thread flashed back to "waiting" and later deltas rebuilt a
 * truncated bubble. Instead: authoritative entries come from
 * history; optimistic/streaming entries survive unless history
 * proves them settled (same user content persisted / streaming
 * delegation already has its persisted assistant).
 */
export function mergeHistory(
  prev: SweaveThreadState,
  messages: SessionMessage[],
): SweaveThreadState {
  const historyUserContents = new Set(
    messages.filter((m) => m.role === "user").map((m) => m.content),
  );
  const historyAssistantDelegations = new Set(
    messages
      .filter((m) => m.role === "assistant")
      .map((m) => delegationIdOf(m))
      .filter((id): id is string => id !== null),
  );
  const kept = prev.entries.filter((e) => {
    if (e.optimistic) {
      // The server persisted our optimistic text: the WS reconcile
      // (or this history) covers it; drop the local copy.
      if (e.message.role === "user" && historyUserContents.has(e.message.content)) {
        return false;
      }
      return true;
    }
    if (e.streaming) {
      // The persisted assistant for this delegation landed while we
      // weren't looking: the bubble is settled, drop it.
      if (e.delegationId && historyAssistantDelegations.has(e.delegationId)) {
        return false;
      }
      return true;
    }
    return false;
  });
  return {
    entries: [
      ...messages.map((message) => ({ message, streaming: false })),
      ...kept,
    ],
    turn: kept.length > 0 ? prev.turn : "idle",
    activeDelegationId: kept.some((e) => e.streaming) ? prev.activeDelegationId : null,
  };
}

/** Synthetic id prefix for optimistic user messages. */
const OPTIMISTIC_PREFIX = "local-";

// ---------------------------------------------------------------------------
// Projection -> ThreadMessageLike[]
// ---------------------------------------------------------------------------

function textContent(text: string, streaming: boolean): ThreadMessageLike["content"] {
  return [
    {
      type: "text",
      text,
      // Per-part status: the strict ThreadMessageLike normalization
      // requires it, and the Text slot components read it.
      status: { type: streaming ? "running" : "complete" },
    } as { type: "text"; text: string },
  ];
}

/**
 * A message flagged ``metadata.superseded`` belongs to a turn the
 * user rewound past (edit + resend / retry). Record, not deletion:
 * the UI renders these collapsed and dimmed.
 */
export function isSuperseded(message: SessionMessage): boolean {
  return (message.metadata ?? {}).superseded === true;
}

/** Project one entry; only user/assistant become thread bubbles. */
export function projectEntry(entry: ChatEntry): ThreadMessageLike | null {
  const { message, streaming } = entry;
  if (message.role !== "user" && message.role !== "assistant") return null;
  // Thinking: live accumulation wins while streaming; otherwise the
  // persisted copy from message metadata (the backend stores the
  // full reasoning text as metadata.thinking on finalize).
  const persistedThinking = message.metadata?.thinking;
  const thinking =
    entry.thinking ??
    (typeof persistedThinking === "string" && persistedThinking ? persistedThinking : null);
  // assistant-ui expects content as Part[] for all messages; the
  // sanctioned metadata bag is `metadata.custom` (surfaced to the UI
  // components via the message state; R4.2 step 2-pre).
  const like: ThreadMessageLike = {
    id: message.id,
    role: message.role,
    content: textContent(message.content, streaming),
    metadata: {
      custom: {
        timestamp: message.timestamp ?? null,
        delegationId: entry.delegationId ?? delegationIdOf(message),
        superseded: isSuperseded(message),
        thinking,
      },
    },
  };
  if (streaming) {
    (like as { status?: unknown }).status = { type: "running" };
  } else if (message.role === "assistant") {
    // Non-running assistant messages need an explicit terminal status
    // (the part-state normalization reads message.status.type).
    (like as { status?: unknown }).status = { type: "complete", reason: "stop" };
  }
  return like;
}

/** Project the full state's entries. */
export function projectThread(state: SweaveThreadState): ThreadMessageLike[] {
  const out: ThreadMessageLike[] = [];
  for (const entry of state.entries) {
    const p = projectEntry(entry);
    if (p) out.push(p);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Event mapping (pure)
// ---------------------------------------------------------------------------

/**
 * Submit a user turn. Optimistically appends the user message and
 * moves the turn to ``queued`` (the serial loop will run it next).
 * The optimistic message is later reconciled by ``message.added``
 * (the user role) matching the ``local-`` id.
 */
export function applySubmit(
  state: SweaveThreadState,
  content: string,
): SweaveThreadState {
  const optimistic: SessionMessage = {
    id: `${OPTIMISTIC_PREFIX}${Date.now()}`,
    role: "user",
    content,
    timestamp: new Date().toISOString(),
    agent: null,
    tool_name: null,
    tool_result: null,
    metadata: {},
  };
  return {
    ...state,
    entries: [
      ...state.entries,
      { message: optimistic, streaming: false, optimistic: true },
    ],
    turn: "queued",
  };
}

/**
 * Apply a ``chat.delta`` event. Appends ``text`` to the streaming
 * assistant bubble keyed by ``delegationId``, creating the bubble on
 * the first delta. Also flips the turn to ``running`` + records the
 * active delegation (fallback in case ``status_changed`` was missed).
 */
export function applyDelta(
  state: SweaveThreadState,
  delegationId: string,
  text: string,
): SweaveThreadState {
  const existing = state.entries.find(
    (e) => e.streaming && e.delegationId === delegationId,
  );
  if (existing) {
    const entries = state.entries.map((e) =>
      e.delegationId === delegationId && e.streaming
        ? {
            ...e,
            message: { ...e.message, content: e.message.content + text },
          }
        : e,
    );
    return { ...state, entries, turn: "running", activeDelegationId: delegationId };
  }

  // First delta of the turn: create the streaming bubble.
  const bubble: SessionMessage = {
    id: `stream-${delegationId}`,
    role: "assistant",
    content: text,
    timestamp: new Date().toISOString(),
    agent: "orchestrator",
    tool_name: null,
    tool_result: null,
    metadata: { delegation_id: delegationId },
  };
  return {
    ...state,
    entries: [
      ...state.entries,
      { message: bubble, streaming: true, delegationId },
    ],
    turn: "running",
    activeDelegationId: delegationId,
  };
}

/**
 * Apply a ``chat.thinking`` event. Appends ``text`` to the streaming
 * bubble's reasoning, keyed by ``delegationId`` — creating the bubble
 * (with empty content) when thinking precedes the first text delta.
 * Also flips the turn to ``running`` + records the active delegation,
 * same fallback contract as ``applyDelta``.
 */
export function applyThinking(
  state: SweaveThreadState,
  delegationId: string,
  text: string,
): SweaveThreadState {
  const existing = state.entries.find(
    (e) => e.streaming && e.delegationId === delegationId,
  );
  if (existing) {
    const entries = state.entries.map((e) =>
      e.delegationId === delegationId && e.streaming
        ? { ...e, thinking: (e.thinking ?? "") + text }
        : e,
    );
    return { ...state, entries, turn: "running", activeDelegationId: delegationId };
  }

  // Thinking before any text: create the streaming bubble early so
  // the Thinking block paints during the reasoning phase.
  const bubble: SessionMessage = {
    id: `stream-${delegationId}`,
    role: "assistant",
    content: "",
    timestamp: new Date().toISOString(),
    agent: "orchestrator",
    tool_name: null,
    tool_result: null,
    metadata: { delegation_id: delegationId },
  };
  return {
    ...state,
    entries: [
      ...state.entries,
      { message: bubble, streaming: true, delegationId, thinking: text },
    ],
    turn: "running",
    activeDelegationId: delegationId,
  };
}

/**
 * Extract the delegation join key from a persisted message's
 * metadata, if present.
 */
export function delegationIdOf(message: SessionMessage): string | null {
  const meta = message.metadata ?? {};
  const id = meta.delegation_id;
  return typeof id === "string" && id ? id : null;
}

/**
 * Apply a ``message.added`` event (user or assistant).
 *
 * - user: reconcile the optimistic message. The optimistic entry (id
 *   prefixed ``local-``) is replaced by the authoritative copy; if
 *   there is no optimistic entry (e.g. a history replay), the message
 *   is appended — with id-dedupe so a re-delivery is a no-op.
 * - assistant: finalize. The streaming bubble (joined by
 *   ``metadata.delegation_id``, or ``activeDelegationId`` as fallback)
 *   is replaced by the authoritative message; the turn returns to
 *   ``idle`` and the active delegation is cleared.
 */
export function applyMessageAdded(
  state: SweaveThreadState,
  message: SessionMessage,
): SweaveThreadState {
  // Id-dedupe: re-delivery of the same persisted message is a no-op.
  if (state.entries.some((e) => !e.streaming && e.message.id === message.id)) {
    return state;
  }

  if (message.role === "user") {
    // Replace ALL optimistic entries (defensive: a race could have
    // created more than one before the UI disabled).
    const entries = state.entries.map((e) =>
      e.optimistic && e.message.id.startsWith(OPTIMISTIC_PREFIX)
        ? { message, streaming: false }
        : e,
    );
    // If no optimistic entry was found, append (id-dedupe above
    // handles the case where stateFromHistory already added it).
    const hadOptimistic = state.entries.some(
      (e) => e.optimistic && e.message.id.startsWith(OPTIMISTIC_PREFIX),
    );
    if (!hadOptimistic) {
      return {
        ...state,
        entries: [...state.entries, { message, streaming: false }],
      };
    }
    return { ...state, entries };
  }

  // assistant -> finalize
  const join = delegationIdOf(message) ?? state.activeDelegationId;
  const streamingIdx = state.entries.findIndex(
    (e) => e.streaming && (join === null || e.delegationId === join),
  );
  if (streamingIdx >= 0) {
    const entries = state.entries.slice();
    entries[streamingIdx] = { message, streaming: false };
    return { ...state, entries, turn: "idle", activeDelegationId: null };
  }
  return {
    ...state,
    entries: [...state.entries, { message, streaming: false }],
    turn: "idle",
    activeDelegationId: null,
  };
}

/**
 * Optimistic rerun (edit + resend / retry). Flags every entry after
 * the target user message superseded and (for edits) swaps its
 * content, then moves the turn to ``queued`` — the WS events for the
 * new turn (status/delta/message.added) drive the rest, exactly like
 * a fresh submit. Returns the SAME state object when the target is
 * missing or not a persisted user message (the caller uses
 * referential equality to decide whether to POST).
 */
export function applyRerun(
  state: SweaveThreadState,
  messageId: string,
  content?: string,
): SweaveThreadState {
  const idx = state.entries.findIndex(
    (e) => !e.streaming && !e.optimistic && e.message.id === messageId,
  );
  if (idx < 0 || state.entries[idx].message.role !== "user") return state;
  const entries = state.entries.map((e, i) => {
    if (i < idx) return e;
    if (i === idx) {
      if (content === undefined || content === e.message.content) return e;
      return { ...e, message: { ...e.message, content } };
    }
    if (isSuperseded(e.message)) return e;
    return { ...e, message: { ...e.message, metadata: { ...e.message.metadata, superseded: true } } };
  });
  return { ...state, entries, turn: "queued", activeDelegationId: null };
}

/**
 * Apply a ``delegation.status_changed`` event. Only the ``running``
 * transition mutates state (queued → running + record the delegation);
 * ``done``/``failed`` are informational — the authoritative close is
 * the assistant ``message.added`` which always follows.
 */
export function applyStatusChanged(
  state: SweaveThreadState,
  delegationId: string,
  status: string,
): SweaveThreadState {
  if (status === "running") {
    return {
      ...state,
      turn: "running",
      activeDelegationId: delegationId,
    };
  }
  return state;
}