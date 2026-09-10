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
  delegationIdOf,
  initialThreadState,
  isSuperseded,
  mergeHistory,
  projectEntry,
  projectThread,
  stateFromHistory,
  type SweaveThreadState,
} from "../runtime";
import type { SessionMessage } from "@/types";

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