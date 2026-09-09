/**
 * R4.2 step 1: adapter state-machine tests.
 *
 * Pins the deterministic event mapping: delta ordering, finalize
 * (``message.added`` replaces the streaming bubble), queue states
 * (idle → queued → running → idle), optimistic-reconcile, and
 * id-dedupe. The mapping is a view projection of our WS stream +
 * REST history into assistant-ui's ``ThreadMessageLike`` shape.
 */
import { describe, it, expect } from "vitest";
import {
  applyDelta,
  applyMessageAdded,
  applyStatusChanged,
  applySubmit,
  delegationIdOf,
  initialThreadState,
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