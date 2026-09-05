/**
 * Tests for the chat streaming patch logic (M1.9 Step 2).
 *
 * The chat surface is the M1.8 streaming invariant made
 * React-friendly: a single streaming bubble is created on the
 * first ``chat.delta`` for a delegation, patched in place on
 * subsequent deltas, and replaced (NOT re-rendered) on the
 * ``message.added`` event. The reducer is pure so the test pins
 * the merge semantics without rendering.
 *
 * The reducer is the canonical "what does the chat list look
 * like after N events" -- a single function. React useReducer
 * wraps it; the tests verify the function directly.
 */
import { describe, it, expect } from "vitest";
import {
  applyChatEvent,
  initialChatState,
  type ChatState,
} from "../chat/reducer";
import type { SessionMessage } from "@/types";

const USER_MSG: SessionMessage = {
  id: "u-1",
  role: "user",
  content: "hi",
  timestamp: "2026-01-01T00:00:00Z",
  agent: null,
  tool_name: null,
  tool_result: null,
  metadata: {},
};

describe("initialChatState", () => {
  it("starts empty", () => {
    const s = initialChatState();
    expect(s.messages).toEqual([]);
    expect(s.streamingByDelegation).toEqual({});
  });
});

describe("applyChatEvent: chat.delta", () => {
  it("creates a streaming bubble for a new delegation id", () => {
    const s = applyChatEvent(initialChatState(), {
      kind: "delta",
      delegationId: "d-1",
      sessionId: "s-1",
      text: "Hello",
    });
    expect(s.streamingByDelegation["d-1"]).toBe("Hello");
    // The bubble is NOT in the messages list yet -- it lives in
    // streamingByDelegation until message.added replaces it.
    expect(s.messages).toEqual([]);
  });

  it("appends subsequent deltas to the same bubble", () => {
    let s = applyChatEvent(initialChatState(), {
      kind: "delta",
      delegationId: "d-1",
      sessionId: "s-1",
      text: "Hello",
    });
    s = applyChatEvent(s, {
      kind: "delta",
      delegationId: "d-1",
      sessionId: "s-1",
      text: " world",
    });
    expect(s.streamingByDelegation["d-1"]).toBe("Hello world");
  });

  it("isolates concurrent streams by delegation id", () => {
    let s = initialChatState();
    s = applyChatEvent(s, { kind: "delta", delegationId: "d-1", sessionId: "s-1", text: "A" });
    s = applyChatEvent(s, { kind: "delta", delegationId: "d-2", sessionId: "s-1", text: "B" });
    expect(s.streamingByDelegation["d-1"]).toBe("A");
    expect(s.streamingByDelegation["d-2"]).toBe("B");
  });
});

describe("applyChatEvent: message.added", () => {
  it("replaces a streaming bubble with the persisted message", () => {
    let s = initialChatState();
    s = applyChatEvent(s, { kind: "delta", delegationId: "d-1", sessionId: "s-1", text: "Hello" });
    const persisted: SessionMessage = {
      id: "a-1",
      role: "assistant",
      content: "Hello world",
      timestamp: "2026-01-01T00:00:01Z",
      agent: "orchestrator",
      tool_name: null,
      tool_result: null,
      metadata: { delegation_id: "d-1" },
    };
    s = applyChatEvent(s, {
      kind: "messageAdded",
      sessionId: "s-1",
      message: persisted,
    });
    expect(s.messages).toEqual([persisted]);
    expect(s.streamingByDelegation["d-1"]).toBeUndefined();
  });

  it("appends a user message directly (no streaming)", () => {
    const s = applyChatEvent(initialChatState(), {
      kind: "messageAdded",
      sessionId: "s-1",
      message: USER_MSG,
    });
    expect(s.messages).toEqual([USER_MSG]);
    expect(s.streamingByDelegation).toEqual({});
  });

  it("appends a tool message (non-streaming) without touching the stream", () => {
    let s = initialChatState();
    s = applyChatEvent(s, { kind: "delta", delegationId: "d-1", sessionId: "s-1", text: "stream" });
    const toolMsg: SessionMessage = {
      ...USER_MSG,
      id: "t-1",
      role: "tool",
      content: "result",
    };
    s = applyChatEvent(s, { kind: "messageAdded", sessionId: "s-1", message: toolMsg });
    expect(s.messages).toEqual([toolMsg]);
    // The streaming bubble is still alive (the tool msg didn't
    // replace it -- only the matching assistant msg does).
    expect(s.streamingByDelegation["d-1"]).toBe("stream");
  });

  it("handles message.added arriving BEFORE any chat.delta (race)", () => {
    // The orchestrator might finish a tiny turn fast enough that
    // message.added lands before the first delta; the reducer
    // should still append the message and not orphan a future
    // delta (the streaming bubble gets created on the first delta
    // and the next message.added with the matching delegation_id
    // replaces it).
    const persisted: SessionMessage = {
      ...USER_MSG,
      role: "assistant",
      content: "fast",
      metadata: { delegation_id: "d-1" },
    };
    let s = applyChatEvent(initialChatState(), {
      kind: "messageAdded",
      sessionId: "s-1",
      message: persisted,
    });
    s = applyChatEvent(s, { kind: "delta", delegationId: "d-1", sessionId: "s-1", text: "fast+more" });
    expect(s.messages).toEqual([persisted]);
    expect(s.streamingByDelegation["d-1"]).toBe("fast+more");
  });
});

describe("applyChatEvent: turn boundary", () => {
  it("clears all streaming bubbles on a turn boundary", () => {
    let s = initialChatState();
    s = applyChatEvent(s, { kind: "delta", delegationId: "d-1", sessionId: "s-1", text: "x" });
    s = applyChatEvent(s, { kind: "delta", delegationId: "d-2", sessionId: "s-1", text: "y" });
    s = applyChatEvent(s, { kind: "turnBoundary", sessionId: "s-1" });
    expect(s.streamingByDelegation).toEqual({});
    // Messages are NOT cleared on a turn boundary (the user
    // expects to see their conversation history).
    expect(s.messages).toEqual([]);
  });
});

describe("applyChatEvent: ordering", () => {
  it("appends messages in the order they arrive", () => {
    let s: ChatState = initialChatState();
    s = applyChatEvent(s, { kind: "messageAdded", sessionId: "s-1", message: USER_MSG });
    const assistant: SessionMessage = {
      ...USER_MSG,
      id: "a-1",
      role: "assistant",
      content: "ok",
    };
    s = applyChatEvent(s, { kind: "messageAdded", sessionId: "s-1", message: assistant });
    expect(s.messages.map((m: SessionMessage) => m.id)).toEqual(["u-1", "a-1"]);
  });
});
