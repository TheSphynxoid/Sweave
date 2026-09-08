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
 *   3. `chat.delta` × N            — streaming deltas, keyed by
 *                                   `delegation_id`.
 *   4. `delegation.status_changed` (done | failed)
 *   5. `message.added` (assistant) — `metadata.delegation_id` is the
 *                                   join key; this replaces the bubble.
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

/** Project one entry; only user/assistant become thread bubbles. */
export function projectEntry(entry: ChatEntry): ThreadMessageLike | null {
  const { message, streaming } = entry;
  if (message.role !== "user" && message.role !== "assistant") return null;
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
    const optimisticIdx = state.entries.findIndex(
      (e) => e.optimistic && e.message.id.startsWith(OPTIMISTIC_PREFIX),
    );
    if (optimisticIdx >= 0) {
      const entries = state.entries.slice();
      entries[optimisticIdx] = { message, streaming: false };
      return { ...state, entries };
    }
    return {
      ...state,
      entries: [...state.entries, { message, streaming: false }],
    };
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