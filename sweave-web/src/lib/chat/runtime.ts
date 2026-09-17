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
 *                                   (delegation_id, round).
 *   3b. `chat.tool` × N            — tool transitions, keyed by
 *                                   (delegation_id, round, callID);
 *                                   latest status wins per callID.
 *   4. `chat.delta` × N            — streaming deltas, keyed by
 *                                   (delegation_id, round).
 *   5. `message.added` (assistant, intermediate, multi-message turns
 *      only) — the round's narration persists (`turn_final: false`);
 *      the turn stays running for the next round.
 *   6. `delegation.status_changed` (done | failed)
 *   7. `message.added` (assistant, final) — `metadata.delegation_id`
 *                                   is the join key (+ `turn_round`);
 *                                   `metadata.thinking` carries the
 *                                   full reasoning text; this replaces
 *                                   the round's bubble.
 *
 * Two hardening deviations from the happy order (2026-09-11 finalize
 * hardening — a stuck `streaming` bubble renders raw markdown as plain
 * text forever):
 *   - a trailing `chat.delta`/`chat.thinking` AFTER the authoritative
 *     `message.added` (WS replay / coalescer tail) must NOT resurrect
 *     the settled bubble or re-stick the composer;
 *   - the crash path (`sweave/chat/loop.py::_crash_finalise`) emits
 *     ONLY `delegation.status_changed` (failed|cancelled) — no
 *     `message.added` — so a streaming bubble for the closing
 *     delegation is promoted to a settled placeholder there, and a
 *     later authoritative `message.added` replaces the placeholder.
 *
 * This module is PURE (no React). The React hook in step 1b wraps it in
 * `useExternalStoreRuntime`. All functions are side-effect free and the
 * state machine is fully deterministic per event sequence -- the vitest
 * suite pins the ordering + finalize + queue semantics.
 */
import type { ChatSegment, ChatToolRow, SessionMessage, TurnSnapshot } from "@/types";
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
  /** Orchestrator round within the turn (0 = first turn, 1 =
      synthesis). Bubbles and finalize match on (delegationId,
      round); absent (legacy) means 0. */
  round?: number;
  /** True for a locally-optimistic user message (not yet confirmed by the server). */
  optimistic?: boolean;
  /** Live-accumulated reasoning text for the in-flight bubble (chat.thinking). */
  thinking?: string;
  /** Live-accumulated compact tool rows for the in-flight bubble
      (chat.tool, latest status wins per callID). */
  tools?: ChatToolRow[];
  /** Live arrival log for the in-flight bubble (one item per delta /
      thinking slice / first-seen tool). The renderer joins contiguous
      same-kind runs, so this projects to the same ordered timeline
      as persisted metadata.segments — no layout jump on finalize. */
  seq?: ChatSegment[];
}

/**
 * Orchestrator round of a persisted message (backend
 * ``metadata.turn_round``; multi-message turns, 2026-09-11).
 * Absent (legacy records, streaming bubbles) means round 0.
 */
export function roundOf(message: SessionMessage): number {
  const v = (message.metadata ?? {}).turn_round;
  return typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : 0;
}

/**
 * False only for intermediate round messages (backend
 * ``metadata.turn_final === false``). Absent (legacy records,
 * optimistic entries) means final — preserving the single-message
 * close semantics for everything the backend didn't mark.
 */
export function isFinal(message: SessionMessage): boolean {
  return (message.metadata ?? {}).turn_final !== false;
}

/**
 * Lane-placement rule (2026-09-17, fixed 2026-09-18): rounds share
 * one chat delegation id, so mounting lanes under every message
 * with that id duplicates the box. While the turn is live the lanes
 * mount on the spawning round (round 0 today — sequential waves
 * will stamp the round at spawn per WAVE_LOOP_PLAN.md ruling 4) so
 * they stay visible across the child-wait gap; once settled they
 * mount on the final message (the visible copy — the spawn round
 * collapses). Pure; Thread.tsx and the pin test share it. Callers
 * still check delegationId.
 */
export interface LanePlacement {
  turnFinal?: boolean | null;
  round?: number | null;
  isActiveTurn?: boolean | null;
}

export function shouldShowLanes(custom: LanePlacement, isRunning: boolean): boolean {
  const isLive = isRunning || custom.isActiveTurn === true;
  const isSpawnRound = (custom.round ?? 0) === 0;
  if (isLive) return isSpawnRound;
  return custom.turnFinal !== false;
}

/**
 * Streaming-bubble id for a (delegation, round) pair. Round 0 keeps
 * the historical ``stream-<id>`` shape (the crash-path placeholder
 * lookup matches on it); later rounds suffix.
 */
export function bubbleIdFor(delegationId: string, round: number): string {
  return round > 0 ? `stream-${delegationId}-r${round}` : `stream-${delegationId}`;
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
    entries: messages.map((message) => ({
      message,
      streaming: false,
      round: roundOf(message),
    })),
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
  // Settled assistants keyed by (delegation, round): a reconnect
  // mid-synthesis must not drop the round-1 bubble just because the
  // round-0 message already persisted.
  const historySettledRounds = new Set(
    messages
      .filter((m) => m.role === "assistant")
      .map((m) => {
        const id = delegationIdOf(m);
        return id === null ? null : `${id}::${roundOf(m)}`;
      })
      .filter((k): k is string => k !== null),
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
      // The persisted assistant for this (delegation, round) landed
      // while we weren't looking: the bubble is settled, drop it.
      if (
        e.delegationId &&
        historySettledRounds.has(`${e.delegationId}::${e.round ?? 0}`)
      ) {
        return false;
      }
      return true;
    }
    return false;
  });
  return {
    entries: [
      ...messages.map((message) => ({
        message,
        streaming: false,
        round: roundOf(message),
      })),
      ...kept,
    ],
    turn: kept.length > 0 ? prev.turn : "idle",
    activeDelegationId: kept.some((e) => e.streaming) ? prev.activeDelegationId : null,
  };
}

/**
 * Restore the streaming/waiting state from an ACTIVE-turn snapshot
 * (2026-09-10 recovery contract).
 *
 * Sources of the snapshot:
 *   - ``GET /api/sessions/{id}/turn`` on page load / session
 *     activation / WS reconnect: ``{active, turn}`` — apply when
 *     ``active`` is true and ``turn`` carries a delegation id.
 *   - HTTP 409 from the turn-send endpoint while a turn runs: the
 *     body is ``detail.turn`` — adopt as if the turn were locally
 *     started (no error).
 *
 * Semantics:
 *   - Only ACTIVE turns are restored: never resurrect text for a
 *     turn that already finalized (the final overwrite is
 *     authoritative and the stream tail on a completed/error record
 *     is a partial, not the truth). Callers must therefore only
 *     hand in snapshots with ``active`` truthy; this function also
 *     guards against a settled local state (a non-streaming entry
 *     joined by the delegation id already proves the turn ended
 *     between snapshot and apply — identity return).
 *   - If the live state already tracks the same delegation (a
 *     reconnect mid-stream is the common case), the LIVE
 *     accumulated text wins over the (older) snapshot text —
 *     deltas may have landed since the snapshot was taken. Only
 *     empty live thinking is backfilled from the snapshot.
 *   - The delegation join key is required to seed the bubble; a
 *     snapshot without one can only bump the turn to ``running``
 *     (waiting indicator, no bubble) — the WS deltas will key the
 *     bubble when they arrive.
 */
export function applyTurnSnapshot(
  state: SweaveThreadState,
  snapshot: TurnSnapshot | null | undefined,
): SweaveThreadState {
  if (!snapshot) return state;
  const delegationId = snapshot.delegation_id;
  if (!delegationId) {
    // No join key: best-effort waiting indicator only; the WS
    // deltas (which carry delegation_id) key the real bubble.
    return state.turn === "idle" ? { ...state, turn: "running" } : state;
  }
  const round =
    typeof snapshot.round === "number" && snapshot.round >= 0
      ? Math.floor(snapshot.round)
      : 0;
  // Already settled for this (delegation, round): the turn finished between
  // the snapshot and now -- never resurrect partial text.
  if (
    state.entries.some(
      (e) =>
        !e.streaming &&
        delegationIdOf(e.message) === delegationId &&
        roundOf(e.message) === round,
    )
  ) {
    return state;
  }
  const existing = state.entries.find(
    (e) => e.streaming && e.delegationId === delegationId && (e.round ?? 0) === round,
  );
  if (existing) {
    // Live text wins over the (older) snapshot accumulation; only
    // empty live thinking is backfilled. Tools union by callID (the
    // snapshot may carry transitions that landed before the socket
    // opened; live rows win on conflict).
    const snapshotTools = Array.isArray(snapshot.tools)
      ? snapshot.tools
          .map((t) => normalizeToolRow(t, round))
          .filter((t): t is ChatToolRow => t !== null)
      : [];
    const entries = state.entries.map((e) =>
      e.streaming && e.delegationId === delegationId && (e.round ?? 0) === round
        ? {
            ...e,
            thinking: e.thinking ?? (snapshot.thinking_text || undefined),
            tools: unionTools(e.tools, snapshotTools),
            seq: unionSeq(e.seq, snapshotTools, e.tools),
          }
        : e,
    );
    return { ...state, entries, turn: "running", activeDelegationId: delegationId };
  }
  // Seed the recovering bubble. The registry's stream_text is the
  // FULL accumulated partial reply (the loop appends to it), so it
  // maps 1:1 onto the bubble content; subsequent chat.delta events
  // append inline.
  const bubble: SessionMessage = {
    id: bubbleIdFor(delegationId, round),
    role: "assistant",
    content: snapshot.stream_text,
    timestamp: new Date().toISOString(),
    agent: "orchestrator",
    tool_name: null,
    tool_result: null,
    metadata: { delegation_id: delegationId },
  };
  const seedTools = Array.isArray(snapshot.tools)
    ? snapshot.tools
        .map((t) => normalizeToolRow(t, round))
        .filter((t): t is ChatToolRow => t !== null)
    : [];
  // Best-effort positions: snapshot tools first (appearance order),
  // then the accumulated thinking/text (no positions survive the
  // snapshot). Transient — live deltas and finalize correct it.
  const seedSeq: ChatSegment[] = [
    ...seedTools.map((t) => ({ kind: "tool" as const, callID: t.callID })),
    ...(snapshot.thinking_text
      ? [{ kind: "thinking" as const, text: snapshot.thinking_text }]
      : []),
    ...(snapshot.stream_text
      ? [{ kind: "text" as const, text: snapshot.stream_text }]
      : []),
  ];
  return {
    ...state,
    entries: [
      ...state.entries,
      {
        message: bubble,
        streaming: true,
        delegationId,
        round,
        thinking: snapshot.thinking_text || undefined,
        tools: seedTools.length > 0 ? seedTools : undefined,
        seq: seedSeq.length > 0 ? seedSeq : undefined,
      },
    ],
    turn: "running",
    activeDelegationId: delegationId,
  };
}

const OPTIMISTIC_PREFIX = "local-";

/**
 * Union two tool-row lists by callID (snapshot recovery): live rows
 * win on conflict; snapshot-only rows append in snapshot order.
 */
function unionTools(
  live: ChatToolRow[] | undefined,
  snapshot: ChatToolRow[],
): ChatToolRow[] | undefined {
  if (snapshot.length === 0) return live;
  const prev = live ?? [];
  const seen = new Set(prev.map((t) => t.callID));
  const extra = snapshot.filter((t) => !seen.has(t.callID));
  if (extra.length === 0) return live;
  return [...prev, ...extra];
}

/**
 * Timeline positions for snapshot-only rows (snapshot recovery):
 * markers append after the live log (positions don't survive the
 * snapshot). Returns the live seq untouched when nothing is missing.
 */
function unionSeq(
  live: ChatSegment[] | undefined,
  snapshot: ChatToolRow[],
  liveTools: ChatToolRow[] | undefined,
): ChatSegment[] | undefined {
  const seen = new Set((liveTools ?? []).map((t) => t.callID));
  const extra = snapshot.filter((t) => !seen.has(t.callID));
  if (extra.length === 0) return live;
  return [
    ...(live ?? []),
    ...extra.map((t) => ({ kind: "tool" as const, callID: t.callID })),
  ];
}

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

/**
 * Compact tool rows persisted on a message (assistant
 * ``metadata.tools[]``, chat transparency). Validated: entries with
 * a missing/empty callID are dropped; unknown shapes degrade to a
 * best-effort row. Empty/absent means "no tools ran this round".
 */
export function toolsOf(message: SessionMessage): ChatToolRow[] {
  const raw = (message.metadata ?? {}).tools;
  if (!Array.isArray(raw)) return [];
  const out: ChatToolRow[] = [];
  for (const t of raw) {
    if (!t || typeof t !== "object") continue;
    const row = t as Record<string, unknown>;
    const callID = typeof row.callID === "string" ? row.callID : "";
    if (!callID) continue;
    out.push({
      callID,
      tool: typeof row.tool === "string" && row.tool ? row.tool : "tool",
      status: typeof row.status === "string" && row.status ? row.status : "unknown",
      summary: typeof row.summary === "string" ? row.summary : "",
      title: typeof row.title === "string" ? row.title : null,
      input:
        row.input && typeof row.input === "object"
          ? (row.input as Record<string, unknown>)
          : null,
      round:
        typeof row.round === "number" && Number.isInteger(row.round) && row.round >= 0
          ? row.round
          : null,
      // TOOL_CARDS step 2: additive detail (legacy servers omit it -> degrade).
      detail: (row.detail as ChatToolRow["detail"]) ?? null,
      output_excerpt:
        typeof row.output_excerpt === "string" ? row.output_excerpt : null,
    });
  }
  return out;
}

/** Project one entry; only user/assistant become thread bubbles. */
export function projectEntry(
  entry: ChatEntry,
  opts?: { isActiveTurn?: boolean },
): ThreadMessageLike | null {
  const { message, streaming } = entry;
  if (message.role !== "user" && message.role !== "assistant") return null;
  // Thinking: live accumulation wins while streaming; otherwise the
  // persisted copy from message metadata (the backend stores the
  // full reasoning text as metadata.thinking on finalize).
  const persistedThinking = message.metadata?.thinking;
  const thinking =
    entry.thinking ??
    (typeof persistedThinking === "string" && persistedThinking ? persistedThinking : null);
  // Ordered timeline (arrival-ordered text/reasoning/tool log):
  // the live op log wins while streaming, else the persisted
  // metadata.segments. Tool markers ({kind: "tool"}) carry the
  // callID; the row lookup is `tools` below. Absent on legacy
  // messages and tool-less text-only turns — readers fall back to
  // the single thinking block + full-text body.
  const rawSegments = message.metadata?.segments;
  const persistedSegments = Array.isArray(rawSegments)
    ? rawSegments.filter((s): s is ChatSegment => {
        if (!s || typeof s !== "object") return false;
        const row = s as { kind?: unknown; text?: unknown; callID?: unknown };
        if (row.kind === "tool") {
          return typeof row.callID === "string" && row.callID.length > 0;
        }
        return (
          (row.kind === "thinking" || row.kind === "text") &&
          typeof row.text === "string" &&
          row.text.length > 0
        );
      })
    : null;
  const liveSeq =
    streaming && entry.seq && entry.seq.length > 0 ? entry.seq : null;
  const segments = liveSeq ?? persistedSegments;
  // assistant-ui expects content as Part[] for all messages; the
  // sanctioned metadata bag is `metadata.custom` (surfaced to the UI
  // components via the message state; R4.2 step 2-pre).
  // Tools: live accumulation wins while streaming; otherwise the
  // persisted rows from message metadata (the backend stores the
  // round's compact rows as metadata.tools on finalize).
  const tools = entry.tools ?? toolsOf(message);
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
        segments,
        round: entry.round ?? roundOf(message),
        turnFinal: isFinal(message),
        tools,
        isActiveTurn: opts?.isActiveTurn ?? false,
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
    const p = projectEntry(entry, { isActiveTurn: isEntryActive(state, entry) });
    if (p) out.push(p);
  }
  return out;
}

/**
 * True while the entry belongs to the turn still running on this
 * session (the lanes + round auto-expand read this, not message
 * finality — an intermediate round message of a live turn keeps its
 * children lane mounted across the child-wait/synthesis gap).
 */
function isEntryActive(state: SweaveThreadState, entry: ChatEntry): boolean {
  if (state.turn === "idle" || entry.optimistic) return false;
  if (entry.message.role !== "assistant") return false;
  const id = entry.delegationId ?? delegationIdOf(entry.message);
  return id !== null && id === state.activeDelegationId;
}

// ---------------------------------------------------------------------------
// Event mapping (pure)
// ---------------------------------------------------------------------------

/** Delegation statuses that end the turn (loop.py _finalise_turn / _crash_finalise). */
const TERMINAL_STATUSES = new Set(["done", "failed", "cancelled"]);

/**
 * True when the state carries proof the (delegation, round) already
 * finalized: a SETTLED assistant entry joined by metadata delegation
 * id + round. Optimistic user entries and persisted USER messages
 * never prove a settle (the persisted user message lands BEFORE the
 * assistant streams), which is why the role is checked. Scoped by
 * round so a settled round-0 message never eats round-1 deltas.
 */
function hasSettledAssistant(
  state: SweaveThreadState,
  delegationId: string,
  round: number = 0,
): boolean {
  return state.entries.some(
    (e) =>
      !e.streaming &&
      !e.optimistic &&
      e.message.role === "assistant" &&
      delegationIdOf(e.message) === delegationId &&
      roundOf(e.message) === round,
  );
}

/**
 * Close the turn on the given entries, but never clobber a turn that
 * still has another live streaming bubble (concurrent impl children /
 * interleaved turns): the close only reaches idle when nothing streams.
 */
function closeTurnIfIdle(
  state: SweaveThreadState,
  entries: ChatEntry[],
): SweaveThreadState {
  if (entries.some((e) => e.streaming)) {
    return { ...state, entries };
  }
  return { ...state, entries, turn: "idle", activeDelegationId: null };
}

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
 *
 * Finalize hardening: a delta for a delegation that ALREADY has its
 * settled assistant (metadata join) is trailing garbage (WS replay /
 * coalescer tail) — it must not resurrect a zombie streaming bubble
 * or flip the turn back to ``running`` (raw-markdown bug, 2026-09-11).
 */
export function applyDelta(
  state: SweaveThreadState,
  delegationId: string,
  text: string,
  round: number = 0,
): SweaveThreadState {
  if (hasSettledAssistant(state, delegationId, round)) return state;
  const existing = state.entries.find(
    (e) => e.streaming && e.delegationId === delegationId && (e.round ?? 0) === round,
  );
  if (existing) {
    const entries = state.entries.map((e) =>
      e.delegationId === delegationId && e.streaming && (e.round ?? 0) === round
        ? {
            ...e,
            message: { ...e.message, content: e.message.content + text },
            seq: text
              ? [...(e.seq ?? []), { kind: "text" as const, text }]
              : e.seq,
          }
        : e,
    );
    return { ...state, entries, turn: "running", activeDelegationId: delegationId };
  }

  // First delta of the (delegation, round): create the streaming bubble.
  const bubble: SessionMessage = {
    id: bubbleIdFor(delegationId, round),
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
      {
        message: bubble,
        streaming: true,
        delegationId,
        round,
        seq: text ? [{ kind: "text" as const, text }] : [],
      },
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
 * same fallback contract as ``applyDelta`` (same trailing-garbage
 * guard: no thinking after this delegation settled).
 */
export function applyThinking(
  state: SweaveThreadState,
  delegationId: string,
  text: string,
  round: number = 0,
): SweaveThreadState {
  if (hasSettledAssistant(state, delegationId, round)) return state;
  const existing = state.entries.find(
    (e) => e.streaming && e.delegationId === delegationId && (e.round ?? 0) === round,
  );
  if (existing) {
    const entries = state.entries.map((e) =>
      e.delegationId === delegationId && e.streaming && (e.round ?? 0) === round
        ? {
            ...e,
            thinking: (e.thinking ?? "") + text,
            seq: text
              ? [...(e.seq ?? []), { kind: "thinking" as const, text }]
              : e.seq,
          }
        : e,
    );
    return { ...state, entries, turn: "running", activeDelegationId: delegationId };
  }

  // Thinking before any text: create the streaming bubble early so
  // the Thinking block paints during the reasoning phase.
  const bubble: SessionMessage = {
    id: bubbleIdFor(delegationId, round),
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
      {
        message: bubble,
        streaming: true,
        delegationId,
        round,
        thinking: text,
        seq: text ? [{ kind: "thinking" as const, text }] : [],
      },
    ],
    turn: "running",
    activeDelegationId: delegationId,
  };
}

/**
 * Normalize one ``chat.tool`` WS payload into a ChatToolRow.
 * Provider-shaped garbage degrades to a best-effort row (never
 * throws — the stream path must not break the thread).
 */
export function normalizeToolRow(tool: unknown, round: number = 0): ChatToolRow | null {
  if (!tool || typeof tool !== "object") return null;
  const row = tool as Record<string, unknown>;
  const callID = typeof row.callID === "string" ? row.callID : "";
  if (!callID) return null;
  return {
    callID,
    tool: typeof row.tool === "string" && row.tool ? row.tool : "tool",
    status: typeof row.status === "string" && row.status ? row.status : "unknown",
    summary: typeof row.summary === "string" ? row.summary : "",
    title: typeof row.title === "string" ? row.title : null,
    input:
      row.input && typeof row.input === "object"
        ? (row.input as Record<string, unknown>)
        : null,
    round,
    // TOOL_CARDS step 2: additive detail (legacy servers omit it -> degrade).
    detail: (row.detail as ChatToolRow["detail"]) ?? null,
    output_excerpt:
      typeof row.output_excerpt === "string" ? row.output_excerpt : null,
  };
}

/**
 * Apply a ``chat.tool`` event. Upserts the row into the streaming
 * bubble's tool list keyed by ``callID`` (latest status wins),
 * creating the bubble (with empty content) when tools precede the
 * first text delta — same early-bubble contract as
 * ``applyThinking``. Also flips the turn to ``running`` + records
 * the active delegation, with the same trailing-garbage guard (no
 * rows after this delegation settled).
 */
export function applyTool(
  state: SweaveThreadState,
  delegationId: string,
  tool: unknown,
  round: number = 0,
): SweaveThreadState {
  if (hasSettledAssistant(state, delegationId, round)) return state;
  const row = normalizeToolRow(tool, round);
  if (!row) return state;
  const upsert = (tools: ChatToolRow[] | undefined): ChatToolRow[] => {
    const prev = tools ?? [];
    const idx = prev.findIndex((t) => t.callID === row.callID);
    if (idx >= 0) {
      const next = prev.slice();
      next[idx] = row;
      return next;
    }
    return [...prev, row];
  };
  const existing = state.entries.find(
    (e) => e.streaming && e.delegationId === delegationId && (e.round ?? 0) === round,
  );
  if (existing) {
    const entries = state.entries.map((e) => {
      if (!(e.delegationId === delegationId && e.streaming && (e.round ?? 0) === round)) {
        return e;
      }
      const tools = upsert(e.tools);
      // First sighting takes a timeline position; status updates
      // only refresh the row (mirrors the backend segments rule).
      const isNew = !(e.tools ?? []).some((t) => t.callID === row.callID);
      return {
        ...e,
        tools,
        seq: isNew
          ? [...(e.seq ?? []), { kind: "tool" as const, callID: row.callID }]
          : e.seq,
      };
    });
    return { ...state, entries, turn: "running", activeDelegationId: delegationId };
  }

  // Tools before any text: create the streaming bubble early so the
  // activity rows paint during the tool phase.
  const bubble: SessionMessage = {
    id: bubbleIdFor(delegationId, round),
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
      {
        message: bubble,
        streaming: true,
        delegationId,
        round,
        tools: [row],
        seq: [{ kind: "tool" as const, callID: row.callID }],
      },
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
 *   ``metadata.delegation_id`` + round, or ``activeDelegationId`` as fallback)
 *   is replaced by the authoritative message; a final message returns
 *   the turn to ``idle``, an intermediate round message keeps it running.
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

  // assistant -> finalize. The streaming bubble (joined by
  // ``metadata.delegation_id`` + round, or ``activeDelegationId`` as
  // fallback) is replaced by the authoritative message. A FINAL
  // message returns the turn to ``idle``; an intermediate round
  // message (``turn_final === false``) keeps the turn running for
  // the next round's bubble.
  const join = delegationIdOf(message) ?? state.activeDelegationId;
  const round = roundOf(message);
  const final = isFinal(message);
  const finish = (entries: ChatEntry[]): SweaveThreadState => {
    if (!final) {
      return { ...state, entries, turn: "running", activeDelegationId: join };
    }
    return closeTurnIfIdle(state, entries);
  };
  const streamingIdx = state.entries.findIndex(
    (e) =>
      e.streaming &&
      (join === null || e.delegationId === join) &&
      (e.round ?? 0) === round,
  );
  if (streamingIdx >= 0) {
    const entries = state.entries.slice();
    entries[streamingIdx] = { message, streaming: false, round };
    return finish(entries);
  }
  // Crash-path hardening: a terminal status may already have promoted
  // the streaming bubble into a settled placeholder (ids are
  // deterministic: stream-<delegationId>[-r<round>]). The late
  // authoritative message REPLACES the placeholder instead of
  // appending a duplicate.
  if (join) {
    const placeholderIdx = state.entries.findIndex(
      (e) =>
        !e.streaming && !e.optimistic && e.message.id === bubbleIdFor(join, round),
    );
    if (placeholderIdx >= 0) {
      const entries = state.entries.slice();
      entries[placeholderIdx] = { message, streaming: false, round };
      return finish(entries);
    }
  }
  return finish([...state.entries, { message, streaming: false, round }]);
}

/**
 * Optimistic rerun (edit + resend / retry). Flags every entry after
 * the target user message superseded and moves the turn to
 * ``queued`` — the WS events for the new turn (status/delta/
 * message.added) drive the rest, exactly like a fresh submit.
 *
 * A retry (no content, or identical text) reuses the target row:
 * the target stays live, only its tail is flagged. An edit mirrors
 * the server's record model (loop.py ``rerun_turn``): the target
 * keeps its ORIGINAL content flagged superseded, and an optimistic
 * revision (``local-`` id, ``fork_from`` linkage) carries the new
 * text — the server's ``message.added`` swaps the revision in by
 * id. Swapping the new text onto the target instead would show the
 * edited text twice once the revision arrives (incident
 * 2026-09-17). Returns the SAME state object when the target is
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
  const target = state.entries[idx];
  const edited =
    content !== undefined && content !== target.message.content;
  const entries = state.entries.map((e, i) => {
    if (i < idx) return e;
    // Retry reuses the live target row (the backend flags only its
    // tail); an edit supersedes the target too (it keeps its
    // original content for the revision pager).
    if (i === idx && !edited) return e;
    if (isSuperseded(e.message)) return e;
    return { ...e, message: { ...e.message, metadata: { ...e.message.metadata, superseded: true } } };
  });
  if (edited) {
    const revision: SessionMessage = {
      id: `${OPTIMISTIC_PREFIX}rev-${Date.now()}`,
      role: "user",
      content: content as string,
      timestamp: new Date().toISOString(),
      agent: null,
      tool_name: null,
      tool_result: null,
      metadata: { fork_from: messageId, revision: true },
    };
    entries.push({ message: revision, streaming: false, optimistic: true });
  }
  return { ...state, entries, turn: "queued", activeDelegationId: null };
}

/**
 * Apply a ``delegation.status_changed`` event. ``running`` transitions
 * to running + records the delegation (queued → running).
 *
 * Terminal statuses: the happy path (status done → message.added) has
 * message.added owning the close, and a bubble-less terminal status
 * stays informational (legacy contract). BUT the crash path
 * (loop.py ``_crash_finalise``) emits ONLY status_changed(failed) —
 * no message.added — so a streaming bubble FOR THE CLOSING delegation
 * is promoted to a settled placeholder here (its projection gets a
 * terminal message status, so the Text part completes and Markdown
 * renders instead of raw plain text) and the composer re-enables.
 * A terminal status for an unrelated delegation (impl child, im-*) is
 * the identity — it must never close a different live turn.
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
  if (!TERMINAL_STATUSES.has(status)) return state;
  const idx = state.entries.findIndex(
    (e) => e.streaming && e.delegationId === delegationId,
  );
  if (idx < 0) return state;
  const entries = state.entries.map((e, i) =>
    i === idx ? { ...e, streaming: false } : e,
  );
  if (entries.some((e) => e.streaming)) {
    // Another bubble is still live: keep the turn running, re-point
    // tracking only if the closed delegation was the tracked one.
    const activeDelegationId =
      state.activeDelegationId === delegationId
        ? entries.find((e) => e.streaming)?.delegationId ?? null
        : state.activeDelegationId;
    return { ...state, entries, activeDelegationId };
  }
  return { ...state, entries, turn: "idle", activeDelegationId: null };
}