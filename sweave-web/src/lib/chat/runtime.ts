/**
 * Sweave chat runtime adapter (R4.2 step 0 — spike skeleton).
 *
 * DECISION (recorded in docs/R4_2_PLAN.md, 2026-09-07):
 * `@assistant-ui/react-opencode` is REJECTED — its `useOpenCodeRuntime`
 * opens an SSE stream straight to the opencode serve from the browser,
 * bypassing our backend funnel (transcript composition, memory,
 * delegation gating, the R4.0 per-Session binding). We adopt
 * `useExternalStoreRuntime` (from `@assistant-ui/react`) with a custom
 * adapter instead: the same primitives, our state.
 *
 * The adapter is a VIEW PROJECTION of our backend contract:
 *   - REST history (`GET /api/sessions/{id}` → messages) is the
 *     source of truth for the thread.
 *   - WS `chat.delta {session_id, delegation_id, text}` patches the
 *     in-flight assistant message (coalesced ~100ms; M1.8 invariant:
 *     patch in place, never re-render the whole thread).
 *   - WS `message.added` finalizes the authoritative persisted message.
 *   - WS `delegation.status_changed` drives turn status (queued /
 *     running / done) — the serial-turn indicator.
 *
 * This module owns the PURE functions (message projection + event
 * mapping). The React hook that wraps them in
 * `useExternalStoreRuntime` lands in R4.2 step 1. Step 0 ships the
 * types + signatures + the trivial projection; the event mapping is
 * stubbed so step 1 fills it against the real WS envelope.
 */
import type { SessionMessage } from "@/types";
import type { ThreadMessageLike } from "@assistant-ui/react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One turn of the orchestrator's serial queue (per session). */
export type SweaveTurnState = "idle" | "queued" | "running";

/** A WS `chat.delta` event (the backend envelope, narrowed). */
export interface ChatDeltaEvent {
  session_id: string;
  delegation_id: string;
  text: string;
}

/** A WS `delegation.status_changed` event (the backend envelope, narrowed). */
export interface DelegationStatusEvent {
  delegation_id: string;
  status: string;
  agent?: string;
  task_id?: string;
}

/**
 * The adapter's message model. A single list carries both the
 * persisted messages (REST history, authoritative) and the
 * in-flight streaming assistant message (the last element while a
 * turn runs). The projection flattens this into
 * ``ThreadMessageLike[]`` for assistant-ui.
 */
export interface SweaveChatMessage {
  /** Persisted message (authoritative) or the optimistic/streaming one. */
  message: SessionMessage;
  /** True when this entry is the in-flight streaming assistant message. */
  streaming: boolean;
  /** The delegation driving the in-flight turn (streaming entries only). */
  delegationId?: string;
}

/**
 * The adapter's full state. ``messages`` is the projection source;
 * ``turn`` is the serial-queue indicator; ``sessionId`` scopes every
 * WS event (events for other sessions are ignored).
 */
export interface SweaveChatRuntimeState {
  sessionId: string | null;
  messages: SweaveChatMessage[];
  turn: SweaveTurnState;
  /** Delegation currently streaming (the one the turn belongs to). */
  activeDelegationId: string | null;
}

export function initialRuntimeState(sessionId: string | null): SweaveChatRuntimeState {
  return { sessionId, messages: [], turn: "idle", activeDelegationId: null };
}

// ---------------------------------------------------------------------------
// Projection: REST history + streaming entries -> ThreadMessageLike[]
// ---------------------------------------------------------------------------

/**
 * Project one persisted/streaming entry into an assistant-ui
 * ``ThreadMessageLike``. Only ``user`` and ``assistant`` roles map
 * to thread messages today; ``system``/``tool`` records are part of
 * the session detail but are NOT rendered as thread bubbles (they
 * belong to the delegation detail view / tool cards, R4.2 step 2).
 *
 * A streaming entry gets ``status: { type: "running" }`` so
 * assistant-ui renders the "still generating" affordance; the
 * finalized entry (post ``message.added``) gets the default
 * complete status.
 */
export function projectMessage(entry: SweaveChatMessage): ThreadMessageLike | null {
  const { message, streaming } = entry;
  if (message.role !== "user" && message.role !== "assistant") return null;
  const content: ThreadMessageLike["content"] = [
    { type: "text", text: message.content },
  ];
  const like: ThreadMessageLike = {
    id: message.id,
    role: message.role,
    content,
  };
  if (streaming) {
    // TODO(R4.2 step 1): confirm the exact status shape against
    // the installed @assistant-ui/react 0.15.18 types when the
    // hook lands. The running status is what turns on the
    // streaming affordance in the Thread.
    (like as { status?: unknown }).status = { type: "running" };
  }
  return like;
}

/**
 * Project the full state's message list. Streaming entries render
 * as their current accumulated text (the coalescer's latest).
 */
export function projectMessages(
  state: SweaveChatRuntimeState,
): ThreadMessageLike[] {
  const out: ThreadMessageLike[] = [];
  for (const entry of state.messages) {
    const projected = projectMessage(entry);
    if (projected) out.push(projected);
  }
  return out;
}

/**
 * Fold the REST history (from ``GET /api/sessions/{id}``) into the
 * initial adapter state. Every persisted message is authoritative;
 * no streaming entries yet.
 */
export function stateFromHistory(
  sessionId: string,
  history: SessionMessage[],
): SweaveChatRuntimeState {
  return {
    sessionId,
    messages: history.map((message) => ({ message, streaming: false })),
    turn: "idle",
    activeDelegationId: null,
  };
}

// ---------------------------------------------------------------------------
// Event mapping (STUBBED — R4.2 step 1 fills these against the real
// WS envelope; the signatures below are the contract).
// ---------------------------------------------------------------------------

/**
 * STUB — apply a ``chat.delta`` event: append ``text`` to the
 * in-flight streaming assistant message (keyed by delegation id),
 * creating the entry on first delta. Scoped to the active session;
 * events for other sessions are ignored.
 */
export function applyChatDelta(
  _state: SweaveChatRuntimeState,
  _event: ChatDeltaEvent,
): SweaveChatRuntimeState {
  // TODO(R4.2 step 1): implement — map the coalesced delta onto
  // the streaming entry for ``event.delegation_id``; set
  // ``turn: "running"`` and ``activeDelegationId`` on the first
  // delta of a turn.
  throw new Error("applyChatDelta: not implemented (R4.2 step 1)");
}

/**
 * STUB — apply a ``message.added`` event: replace the streaming
 * entry (matched by ``metadata.delegation_id``) with the
 * authoritative persisted message; clear the active delegation;
 * mark the turn complete.
 */
export function applyMessageAdded(
  _state: SweaveChatRuntimeState,
  _message: SessionMessage,
): SweaveChatRuntimeState {
  // TODO(R4.2 step 1): implement — the authoritative finalize
  // (M1.8 invariant: message.added replaces the bubble).
  throw new Error("applyMessageAdded: not implemented (R4.2 step 1)");
}

/**
 * STUB — apply a ``delegation.status_changed`` event: update the
 * serial-queue indicator (queued → running → done/failed). The
 * ``done`` transition matches the turn boundary.
 */
export function applyStatusChanged(
  _state: SweaveChatRuntimeState,
  _event: DelegationStatusEvent,
): SweaveChatRuntimeState {
  // TODO(R4.2 step 1): implement — turn-status projection; the
  // "queued" status drives the serial-queue indicator.
  throw new Error("applyStatusChanged: not implemented (R4.2 step 1)");
}
