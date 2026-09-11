/**
 * R4.2 step 1: adapter state-machine tests.
 *
 * Pins the deterministic event mapping: delta ordering, finalize
 * (``message.added`` replaces the streaming bubble), queue states
 * (idle → queued → running → idle), optimistic-reconcile, and
 * id-dedupe. The mapping is a view projection of our WS stream +
 * REST history into assistant-ui's ``ThreadMessageLike`` shape.
 *
 * Thinking capture: ``chat.thinking`` increments accumulate on the
 * streaming bubble (creating it when thinking precedes text) and
 * the persisted ``metadata.thinking`` copy survives finalize.
 */
import { describe, it, expect } from "vitest";
import {
  applyDelta,
  applyMessageAdded,
  applyRerun,
  applyStatusChanged,
  applySubmit,
  applyThinking,
  applyTurnSnapshot,
  delegationIdOf,
  initialThreadState,
  isSuperseded,
  mergeHistory,
  projectEntry,
  projectThread,
  stateFromHistory,
  type SweaveThreadState,
} from "../runtime";
import type { SessionMessage, TurnSnapshot } from "@/types";

function userMessage(id: string, content: string): SessionMessage {
  return {
    id,
    role: "user",
    content,
    timestamp: "2026-09-07T00:00:00Z",
    agent: null,
    tool_name: null,
    tool_result: null,
    metadata: {},
  };
}

function assistantMessage(
  id: string,
  content: string,
  delegationId?: string,
): SessionMessage {
  return {
    id,
    role: "assistant",
    content,
    timestamp: "2026-09-07T00:00:00Z",
    agent: "orchestrator",
    tool_name: null,
    tool_result: null,
    metadata: delegationId ? { delegation_id: delegationId } : {},
  };
}

describe("projection: state -> ThreadMessageLike[]", () => {
  it("projects user + assistant messages; drops system/tool", () => {
    const state: SweaveThreadState = {
      entries: [
        { message: userMessage("u1", "hi"), streaming: false },
        { message: assistantMessage("a1", "hello"), streaming: false },
        {
          message: {
            id: "t1",
            role: "tool",
            content: "tool output",
            timestamp: "",
            agent: null,
            tool_name: "bash",
            tool_result: null,
            metadata: {},
          },
          streaming: false,
        },
      ],
      turn: "idle",
      activeDelegationId: null,
    };
    const projected = projectThread(state);
    expect(projected.map((m) => m.id)).toEqual(["u1", "a1"]);
  });

  it("marks a streaming entry as running", () => {
    const p = projectEntry({
      message: assistantMessage("a1", "partial"),
      streaming: true,
      delegationId: "chat-1",
    });
    expect(p).not.toBeNull();
    expect((p as { status?: unknown }).status).toEqual({ type: "running" });
  });
});

describe("submit: optimistic user + queued", () => {
  it("appends an optimistic user and sets turn to queued", () => {
    const s = applySubmit(initialThreadState(), "hello");
    expect(s.turn).toBe("queued");
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].optimistic).toBe(true);
    expect(s.entries[0].message.role).toBe("user");
    expect(s.entries[0].message.id.startsWith("local-")).toBe(true);
  });
});

describe("delta ordering: streaming bubbles", () => {
  it("creates the bubble on first delta + appends on subsequent", () => {
    let s = applyDelta(initialThreadState(), "chat-abc", "Hello");
    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBe("chat-abc");
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].message.content).toBe("Hello");

    s = applyDelta(s, "chat-abc", " world");
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].message.content).toBe("Hello world");
  });

  it("tracks two distinct delegations as two bubbles", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "A");
    s = applyDelta(s, "chat-2", "B");
    expect(s.entries).toHaveLength(2);
    // Both are streaming, distinct delegation keys.
    expect(s.entries.map((e) => e.delegationId)).toEqual(["chat-1", "chat-2"]);
  });
});

describe("finalize: message.added(assistant) replaces the bubble", () => {
  it("replaces the streaming bubble and returns to idle", () => {
    let s = applySubmit(initialThreadState(), "do the thing");
    s = applyStatusChanged(s, "chat-abc", "running");
    s = applyDelta(s, "chat-abc", "I am");
    s = applyDelta(s, "chat-abc", " working");

    // The authoritative assistant arrives with metadata.delegation_id.
    s = applyMessageAdded(s, assistantMessage("a1", "I am working", "chat-abc"));

    expect(s.turn).toBe("idle");
    expect(s.activeDelegationId).toBeNull();
    // The streaming bubble is gone; the authoritative assistant is appended.
    const streaming = s.entries.filter((e) => e.streaming);
    expect(streaming).toHaveLength(0);
    const assistant = s.entries.find((e) => e.message.id === "a1");
    expect(assistant).toBeDefined();
    expect(assistant?.message.content).toBe("I am working");
  });
});

describe("optimistic reconcile: message.added(user)", () => {
  it("replaces the optimistic user message with the authoritative one", () => {
    let s = applySubmit(initialThreadState(), "hello");
    expect(s.entries).toHaveLength(1);
    const optimisticId = s.entries[0].message.id;

    s = applyMessageAdded(s, userMessage("real-1", "hello"));

    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].message.id).toBe("real-1");
    expect(s.entries[0].optimistic).toBeUndefined();
    // The synthetic local- id is gone.
    expect(optimisticId.startsWith("local-")).toBe(true);
    expect(s.entries.some((e) => e.message.id === optimisticId)).toBe(false);
  });
});

describe("dedupe: id-based no-op", () => {
  it("ignores a re-delivered assistant message", () => {
    let s = stateFromHistory([userMessage("u1", "hi"), assistantMessage("a1", "yo")]);
    const before = s.entries.length;
    s = applyMessageAdded(s, assistantMessage("a1", "yo"));
    expect(s.entries).toHaveLength(before);
  });
});

describe("status_changed: queue states", () => {
  it("running flips queued -> running; done is informational (no close)", () => {
    let s = applySubmit(initialThreadState(), "x");
    expect(s.turn).toBe("queued");

    s = applyStatusChanged(s, "chat-abc", "running");
    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBe("chat-abc");

    s = applyStatusChanged(s, "chat-abc", "done");
    // done does NOT close the turn -- finalize (message.added) owns it.
    expect(s.turn).toBe("running");
  });
});

describe("stateFromHistory", () => {
  it("loads authoritative history with no streaming entries", () => {
    const s = stateFromHistory([userMessage("u1", "a"), assistantMessage("a1", "b")]);
    expect(s.turn).toBe("idle");
    expect(s.entries).toHaveLength(2);
    expect(s.entries.every((e) => !e.streaming)).toBe(true);
  });
});

describe("delegationIdOf", () => {
  it("reads metadata.delegation_id when present", () => {
    expect(delegationIdOf(assistantMessage("a1", "x", "chat-42"))).toBe("chat-42");
    expect(delegationIdOf(assistantMessage("a1", "x"))).toBeNull();
  });
});

describe("mergeHistory: history reloads keep the in-flight turn", () => {
  it("preserves the optimistic user + streaming bubble across a refetch", () => {
    let s = applySubmit(initialThreadState(), "hello");
    s = applyStatusChanged(s, "chat-abc", "running");
    s = applyDelta(s, "chat-abc", "partial");
    // The server has persisted the user message; history carries it.
    const merged = mergeHistory(s, [userMessage("real-1", "hello")]);
    // Optimistic copy reconciled against the persisted user...
    expect(merged.entries.some((e) => e.optimistic)).toBe(false);
    // ...but the streaming bubble survives and the turn stays live.
    expect(merged.turn).toBe("running");
    expect(merged.activeDelegationId).toBe("chat-abc");
    const bubble = merged.entries.find((e) => e.streaming);
    expect(bubble?.message.content).toBe("partial");
  });

  it("drops a streaming bubble whose assistant already persisted", () => {
    let s = applyDelta(initialThreadState(), "chat-abc", "partial");
    // Refetch after the turn finalized without us seeing message.added.
    const merged = mergeHistory(s, [
      userMessage("u1", "hi"),
      assistantMessage("a1", "full answer", "chat-abc"),
    ]);
    expect(merged.entries.some((e) => e.streaming)).toBe(false);
    expect(merged.turn).toBe("idle");
    expect(merged.activeDelegationId).toBeNull();
  });

  it("resets to idle when nothing is in flight", () => {
    const s = stateFromHistory([userMessage("u1", "a")]);
    const merged = mergeHistory(s, [userMessage("u1", "a"), assistantMessage("a1", "b")]);
    expect(merged.turn).toBe("idle");
    expect(merged.entries).toHaveLength(2);
  });
});

describe("thinking: chat.thinking accumulates on the bubble", () => {
  it("creates the bubble when thinking precedes the first delta", () => {
    let s = applyThinking(initialThreadState(), "chat-abc", "Let me consider");
    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBe("chat-abc");
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].streaming).toBe(true);
    expect(s.entries[0].message.content).toBe("");
    expect(s.entries[0].thinking).toBe("Let me consider");

    // Text deltas then land on the same bubble, thinking intact.
    s = applyDelta(s, "chat-abc", "Hello");
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].message.content).toBe("Hello");
    expect(s.entries[0].thinking).toBe("Let me consider");

    s = applyThinking(s, "chat-abc", "…more");
    expect(s.entries[0].thinking).toBe("Let me consider…more");
  });

  it("projects live thinking into metadata.custom", () => {
    let s = applyThinking(initialThreadState(), "chat-abc", "hmm");
    const projected = projectThread(s);
    expect(projected).toHaveLength(1);
    const custom = projected[0].metadata as { custom?: { thinking?: string | null } };
    expect(custom.custom?.thinking).toBe("hmm");
  });

  it("finalize replaces the bubble but keeps persisted thinking", () => {
    let s = applyThinking(initialThreadState(), "chat-abc", "live thought");
    s = applyDelta(s, "chat-abc", "partial");
    const persisted = {
      ...assistantMessage("a1", "full answer", "chat-abc"),
      metadata: { delegation_id: "chat-abc", thinking: "full thought" },
    };
    s = applyMessageAdded(s, persisted);
    expect(s.turn).toBe("idle");
    expect(s.entries.filter((e) => e.streaming)).toHaveLength(0);
    const projected = projectThread(s);
    const custom = projected.find((m) => m.id === "a1")?.metadata as {
      custom?: { thinking?: string | null };
    };
    expect(custom.custom?.thinking).toBe("full thought");
  });

  it("projects no thinking (null) when neither live nor persisted exists", () => {
    const projected = projectThread(
      stateFromHistory([assistantMessage("a1", "plain", "chat-1")]),
    );
    const custom = projected[0].metadata as { custom?: { thinking?: string | null } };
    expect(custom.custom?.thinking).toBeNull();
  });
});

describe("rerun: edit + resend / retry (supersede, don't delete)", () => {
  function twoTurns(): SweaveThreadState {
    return stateFromHistory([
      userMessage("u1", "first q"),
      assistantMessage("a1", "first a", "chat-1"),
      userMessage("u2", "second q"),
      assistantMessage("a2", "second a", "chat-2"),
    ]);
  }

  it("retry flags later messages superseded and queues the turn", () => {
    const s = applyRerun(twoTurns(), "u1");
    expect(s.turn).toBe("queued");
    expect(s.activeDelegationId).toBeNull();
    const byId = Object.fromEntries(s.entries.map((e) => [e.message.id, e.message]));
    expect(isSuperseded(byId["u1"])).toBe(false);
    expect(byId["u1"].content).toBe("first q");
    expect(isSuperseded(byId["a1"])).toBe(true);
    expect(isSuperseded(byId["u2"])).toBe(true);
    expect(isSuperseded(byId["a2"])).toBe(true);
    // Record, not deletion: nothing removed.
    expect(s.entries).toHaveLength(4);
  });

  it("edit swaps the content and flags the rest", () => {
    const s = applyRerun(twoTurns(), "u2", "second q, edited");
    const byId = Object.fromEntries(s.entries.map((e) => [e.message.id, e.message]));
    expect(byId["u2"].content).toBe("second q, edited");
    expect(isSuperseded(byId["u2"])).toBe(false);
    expect(isSuperseded(byId["a2"])).toBe(true);
    // Earlier turns untouched.
    expect(isSuperseded(byId["u1"])).toBe(false);
    expect(isSuperseded(byId["a1"])).toBe(false);
  });

  it("is a no-op (same ref) for missing or non-user targets", () => {
    const s = twoTurns();
    expect(applyRerun(s, "nope")).toBe(s);
    expect(applyRerun(s, "a1")).toBe(s);
  });

  it("projects the superseded flag into metadata.custom", () => {
    const s = applyRerun(twoTurns(), "u1");
    const projected = projectThread(s);
    const customOf = (id: string) =>
      (projected.find((m) => m.id === id)?.metadata as { custom?: { superseded?: boolean } })
        ?.custom;
    expect(customOf("u1")?.superseded).toBe(false);
    expect(customOf("a1")?.superseded).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Turn recovery (2026-09-10 contract): applyTurnSnapshot
// ---------------------------------------------------------------------------

function turnSnapshot(overrides: Partial<TurnSnapshot> = {}): TurnSnapshot {
  return {
    session_id: "s-1",
    delegation_id: "chat-active",
    status: "running",
    phase: "streaming",
    started_at: "2026-09-10T12:00:00Z",
    stream_text: "Partial reply so far",
    thinking_text: "",
    pending_question: false,
    ...overrides,
  };
}

function userMsg(id: string, content: string): SessionMessage {
  return {
    id,
    role: "user",
    content,
    timestamp: "2026-09-10T11:59:00Z",
    agent: null,
    tool_name: null,
    tool_result: null,
    metadata: {},
  };
}

describe("turn recovery: applyTurnSnapshot", () => {
  it("seeds the streaming bubble + running turn from an active snapshot", () => {
    const base = stateFromHistory([userMsg("u1", "do it")]);
    const s = applyTurnSnapshot(base, turnSnapshot());

    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBe("chat-active");
    const bubble = s.entries.find((e) => e.streaming);
    expect(bubble).toBeDefined();
    expect(bubble?.message.content).toBe("Partial reply so far");
    expect(bubble?.message.id).toBe("stream-chat-active");
    // Projected as running -> the waiting/streaming indicator shows
    // and the composer derives isRunning === true (disabled).
    const projected = projectThread(s);
    const like = projected.find((m) => m.id === "stream-chat-active");
    expect((like as { status?: unknown }).status).toEqual({ type: "running" });
  });

  it("seeds thinking from the snapshot alongside partial text", () => {
    const s = applyTurnSnapshot(
      initialThreadState(),
      turnSnapshot({ thinking_text: "recorded reasoning", stream_text: "text..." }),
    );
    const bubble = s.entries[0];
    expect(bubble.streaming).toBe(true);
    expect(bubble.thinking).toBe("recorded reasoning");
    const projected = projectEntry(bubble);
    const custom = (projected?.metadata ?? {}) as {
      custom?: { thinking?: string | null };
    };
    expect(custom.custom?.thinking).toBe("recorded reasoning");
  });

  it("completion event clears the restored turn and re-enables the composer", () => {
    let s = applyTurnSnapshot(initialThreadState(), turnSnapshot());
    expect(s.turn).toBe("running");

    // The turn's own WS events keep flowing; the authoritative close.
    expect(s.entries.filter((e) => e.streaming)).toHaveLength(1);
    s = applyDelta(s, "chat-active", " more");
    s = applyMessageAdded(s, assistantMessage("a1", "Full final reply", "chat-active"));

    expect(s.turn).toBe("idle");
    expect(s.activeDelegationId).toBeNull();
    expect(s.entries.filter((e) => e.streaming)).toHaveLength(0);
    const final = s.entries.find((e) => e.message.id === "a1");
    expect(final?.message.content).toBe("Full final reply");
    // Assistant finalize replaces the recovering bubble entirely.
    expect(s.entries.some((e) => e.message.id === "stream-chat-active")).toBe(false);
  });

  it("no change on idle: null snapshot is the identity", () => {
    const base = stateFromHistory([userMsg("u1", "hi")]);
    expect(applyTurnSnapshot(base, null)).toBe(base);
    expect(applyTurnSnapshot(base, undefined)).toBe(base);
  });

  it("never resurrects a settled turn from a late snapshot", () => {
    // History already carries the persisted assistant for this
    // delegation (the turn finished between snapshot and apply).
    const settled = stateFromHistory([
      userMsg("u1", "do it"),
      assistantMessage("a1", "final", "chat-active"),
    ]);
    const s = applyTurnSnapshot(settled, turnSnapshot());
    expect(s).toBe(settled);
    expect(s.turn).toBe("idle");
    expect(s.entries.some((e) => e.streaming)).toBe(false);
  });

  it("reconnect mid-stream: live text wins over the older snapshot", () => {
    let s = applyDelta(initialThreadState(), "chat-active", "summary: ");
    s = applyDelta(s, "chat-active", "live tail");
    const s2 = applyTurnSnapshot(s, turnSnapshot({ stream_text: "older partial" }));

    expect(s2.turn).toBe("running");
    const bubble = s2.entries.find((e) => e.streaming);
    // Live accumulation ("summary: live tail") beats the snapshot's
    // older accumulation, but the turn is re-confirmed as running
    // (the reconnect could have raced a poll that just finalized).
    expect(bubble?.message.content).toBe("summary: live tail");
  });

  it("deltas landing after a snapshot restore append to the recovered bubble", () => {
    let s = applyTurnSnapshot(initialThreadState(), turnSnapshot());
    s = applyDelta(s, "chat-active", " appended");
    const bubble = s.entries.find((e) => e.streaming);
    expect(bubble?.message.content).toBe("Partial reply so far appended");
    expect(s.entries).toHaveLength(1);
    expect(s.turn).toBe("running");
  });

  it("history merge keeps the recovered turn alive across a refetch", () => {
    let s = applyTurnSnapshot(initialThreadState(), turnSnapshot());
    // A React Query refetch mid-turn (window refocus) brings the
    // persisted user message; the recovered bubble must survive.
    s = mergeHistory(s, [userMsg("u1", "do it")]);
    expect(s.turn).toBe("running");
    expect(s.entries.some((e) => e.streaming)).toBe(true);
    expect(s.entries.find((e) => e.streaming)?.message.content).toBe(
      "Partial reply so far",
    );
  });

  it("snapshot without a delegation id still flips the waiting indicator", () => {
    const s = applyTurnSnapshot(initialThreadState(), turnSnapshot({ delegation_id: null }));
    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBeNull();
    expect(s.entries).toHaveLength(0);
  });

  it("already-streaming state is not bumped to a new turn / entries not duplicated", () => {
    let s = applyTurnSnapshot(initialThreadState(), turnSnapshot());
    // The server's next re-poll returns the same active snapshot; a
    // re-apply must stay idempotent (one bubble).
    const s2 = applyTurnSnapshot(s, turnSnapshot());
    expect(s2.entries.filter((e) => e.streaming)).toHaveLength(1);
    expect(s2).not.toBe(s); // new object, same number of bubbles
    // Content untouched (no double prepend of the snapshot text).
    expect(s2.entries[0].message.content).toBe("Partial reply so far");
  });
});

describe("rounds: multi-message turns (2026-09-11)", () => {
  function roundMessage(
    id: string,
    content: string,
    delegationId: string,
    round: number,
    turnFinal: boolean,
  ): SessionMessage {
    return {
      id,
      role: "assistant",
      content,
      timestamp: "2026-09-07T00:00:00Z",
      agent: "orchestrator",
      tool_name: null,
      tool_result: null,
      metadata: {
        delegation_id: delegationId,
        turn_round: round,
        turn_final: turnFinal,
      },
    };
  }

  it("deltas scope bubbles per round", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "first ", 0);
    s = applyDelta(s, "chat-1", "second", 1);
    const bubbles = s.entries.filter((e) => e.streaming);
    expect(bubbles).toHaveLength(2);
    expect(bubbles.map((e) => e.message.id)).toEqual([
      "stream-chat-1",
      "stream-chat-1-r1",
    ]);
    expect(bubbles.map((e) => e.message.content)).toEqual(["first ", "second"]);
  });

  it("a settled round-0 message does not eat round-1 deltas", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "first");
    s = applyMessageAdded(s, roundMessage("m0", "first", "chat-1", 0, false));
    expect(s.turn).toBe("running");
    s = applyDelta(s, "chat-1", "synth", 1);
    const bubbles = s.entries.filter((e) => e.streaming);
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].message.content).toBe("synth");
  });

  it("intermediate finalize keeps the turn running; final closes it", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "first");
    s = applyMessageAdded(s, roundMessage("m0", "first", "chat-1", 0, false));
    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBe("chat-1");
    s = applyDelta(s, "chat-1", "synth", 1);
    s = applyMessageAdded(s, roundMessage("m1", "synth", "chat-1", 1, true));
    expect(s.turn).toBe("idle");
    expect(s.activeDelegationId).toBeNull();
    const settled = s.entries.filter((e) => !e.streaming);
    expect(settled.map((e) => e.message.id)).toEqual(["m0", "m1"]);
  });

  it("finalize replaces only its own round's bubble", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "first");
    s = applyDelta(s, "chat-1", "synth", 1);
    s = applyMessageAdded(s, roundMessage("m1", "synth!", "chat-1", 1, true));
    const r0 = s.entries.find((e) => e.streaming);
    expect(r0?.message.content).toBe("first");
    // Round 1 settled but the turn stays running (more rounds may come).
    expect(s.turn).toBe("running");
  });

  it("history merge keeps a live round-1 bubble despite a settled round-0", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "first");
    s = applyMessageAdded(s, roundMessage("m0", "first", "chat-1", 0, false));
    s = applyDelta(s, "chat-1", "synth", 1);
    const history = [
      userMessage("u1", "do it"),
      roundMessage("m0", "first", "chat-1", 0, false),
    ];
    const s2 = mergeHistory(s, history);
    expect(s2.turn).toBe("running");
    expect(s2.entries.some((e) => e.streaming)).toBe(true);
  });

  it("projection carries round + turnFinal into custom", () => {
    const p = projectEntry({
      message: roundMessage("m0", "first", "chat-1", 0, false),
      streaming: false,
    });
    const custom = (p?.metadata as { custom?: Record<string, unknown> } | undefined)
      ?.custom;
    expect(custom?.round).toBe(0);
    expect(custom?.turnFinal).toBe(false);
  });

  it("legacy messages without round metadata default to round 0 / final", () => {
    const p = projectEntry({
      message: assistantMessage("a1", "old", "chat-1"),
      streaming: false,
    });
    const custom = (p?.metadata as { custom?: Record<string, unknown> } | undefined)
      ?.custom;
    expect(custom?.round).toBe(0);
    expect(custom?.turnFinal).toBe(true);
  });

  it("snapshot round seeds the matching bubble", () => {
    const s = applyTurnSnapshot(
      initialThreadState(),
      turnSnapshot({ round: 1, stream_text: "synth so far" }),
    );
    const bubble = s.entries.find((e) => e.streaming);
    expect(bubble?.message.id).toBe("stream-chat-active-r1");
    expect(bubble?.message.content).toBe("synth so far");
  });
});