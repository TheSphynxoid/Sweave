/**
 * Finalize hardening (2026-09-11 bug: "raw markdown persists after the
 * turn completes").
 *
 * Root cause chain: a settled assistant message can only render as
 * plain text when its entry is still ``streaming: true`` (assistant-ui
 * derives the Text part's status from the MESSAGE status; a terminal
 * message status forces the part status to complete — see
 * @assistant-ui/core thread-message-client.js). Two event sequences
 * strand the streaming bubble:
 *
 *   (1) a trailing ``chat.delta`` AFTER ``message.added`` resurrects a
 *       zombie streaming bubble (the exact failure the backend's
 *       ``_finish`` docstring documents; WS reorder/replay re-arms it).
 *       It also re-sticks the composer (turn back to "running").
 *   (2) the crash path (sweave/chat/loop.py ``_crash_finalise``) emits
 *       ONLY ``delegation.status_changed (failed|cancelled)`` — no
 *       ``message.added`` — so the bubble must be closed by the
 *       terminal status itself, and a late authoritative
 *       ``message.added`` must REPLACE the closed placeholder instead
 *       of appending a duplicate.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  applyDelta,
  applyMessageAdded,
  applyStatusChanged,
  applyThinking,
  initialThreadState,
  projectEntry,
  projectThread,
} from "../runtime";
import { useSweaveChatRuntime } from "../useSweaveChatRuntime";
import { api } from "@/api/client";
import type { SessionDetail, SessionMessage, WSEnvelope } from "@/types";

// ---------------------------------------------------------------------------
// Pure reducer tests
// ---------------------------------------------------------------------------

function assistantMessage(
  id: string,
  content: string,
  delegationId?: string,
): SessionMessage {
  return {
    id,
    role: "assistant",
    content,
    timestamp: "2026-09-11T00:00:00Z",
    agent: "orchestrator",
    tool_name: null,
    tool_result: null,
    metadata: delegationId ? { delegation_id: delegationId } : {},
  };
}

describe("finalize hardening: trailing chat.delta after message.added", () => {
  it("does NOT resurrect a zombie streaming bubble (turn stays idle)", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "Hello **wor");
    s = applyMessageAdded(s, assistantMessage("a1", "Hello **world**", "chat-1"));
    expect(s.turn).toBe("idle");

    // Trailing delta (WS reorder / coalescer tail / replay).
    s = applyDelta(s, "chat-1", "ld");

    expect(s.entries.filter((e) => e.streaming)).toHaveLength(0);
    expect(s.turn).toBe("idle");
    expect(s.activeDelegationId).toBeNull();
    // The authoritative copy is the ONLY assistant text in the thread.
    const assistants = s.entries.filter((e) => e.message.role === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0].message.content).toBe("Hello **world**");
  });

  it("trailing chat.thinking after finalize is ignored the same way", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "answer text");
    s = applyMessageAdded(s, assistantMessage("a1", "answer text", "chat-1"));
    s = applyThinking(s, "chat-1", "late reasoning");

    expect(s.entries.filter((e) => e.streaming)).toHaveLength(0);
    expect(s.turn).toBe("idle");
    expect(s.entries.filter((e) => e.message.role === "assistant")).toHaveLength(1);
  });

  it("deltas for a DIFFERENT (still live) delegation keep streaming normally", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "done turn");
    s = applyMessageAdded(s, assistantMessage("a1", "done turn", "chat-1"));
    s = applyDelta(s, "chat-2", "next turn streams");

    expect(s.turn).toBe("running");
    expect(s.entries.filter((e) => e.streaming)).toHaveLength(1);
    expect(s.entries.find((e) => e.streaming)?.delegationId).toBe("chat-2");
  });
});

describe("finalize hardening: terminal status without message.added (crash path)", () => {
  it("status_changed(failed) closes the streaming bubble and unblocks the composer", () => {
    // _crash_finalise emits ONLY status_changed(failed): no message.added.
    let s = applyDelta(initialThreadState(), "chat-1", "partial **text");
    s = applyStatusChanged(s, "chat-1", "failed");

    expect(s.entries.filter((e) => e.streaming)).toHaveLength(0);
    expect(s.turn).toBe("idle");
    expect(s.activeDelegationId).toBeNull();
    // The promoted placeholder projects a TERMINAL message status so
    // the Text part is complete -> Markdown renders (not plain text).
    const projected = projectThread(s);
    const like = projected.find((m) => m.id === "stream-chat-1");
    expect(like).toBeDefined();
    expect((like as { status?: unknown }).status).toEqual({
      type: "complete",
      reason: "stop",
    });
    const part = (like!.content as unknown as { status?: { type: string } }[])[0];
    expect(part?.status?.type).toBe("complete");
  });

  it("status_changed(done) arriving before message.added is also fine (no stuck state)", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "partial");
    s = applyStatusChanged(s, "chat-1", "done");
    expect(s.entries.filter((e) => e.streaming)).toHaveLength(0);
    expect(s.turn).toBe("idle");
  });

  it("the late authoritative message.added REPLACES the promoted placeholder (no duplicate)", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "partial **text");
    s = applyStatusChanged(s, "chat-1", "failed");
    s = applyMessageAdded(s, assistantMessage("a1", "full **answer**", "chat-1"));

    expect(s.entries.filter((e) => e.message.role === "assistant")).toHaveLength(1);
    const final = s.entries.find((e) => e.message.id === "a1");
    expect(final?.message.content).toBe("full **answer**");
    expect(s.entries.some((e) => e.message.id === "stream-chat-1")).toBe(false);
    expect(s.turn).toBe("idle");
  });

  it("normal order (done -> message.added) still finalizes exactly once", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "streaming **tail");
    s = applyStatusChanged(s, "chat-1", "done");
    s = applyMessageAdded(s, assistantMessage("a1", "full **answer**", "chat-1"));

    expect(s.entries.filter((e) => e.message.role === "assistant")).toHaveLength(1);
    expect(s.entries.find((e) => e.message.id === "a1")).toBeDefined();
    expect(s.entries.some((e) => e.message.id === "stream-chat-1")).toBe(false);
  });

  it("terminal status for an UNRELATED delegation (impl child) is a no-op", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "still streaming");
    const before = s;

    s = applyStatusChanged(s, "impl-9", "done");
    expect(s).toBe(before);
    expect(s.entries.filter((e) => e.streaming)).toHaveLength(1);
    expect(s.turn).toBe("running");
  });

  it("a failed status for one bubble does not close a concurrent different turn", () => {
    let s = applyDelta(initialThreadState(), "chat-1", "A");
    s = applyDelta(s, "chat-2", "B");
    s = applyStatusChanged(s, "chat-1", "failed");

    // chat-1's bubble closed; the turn still tracks chat-2.
    expect(s.entries.filter((e) => e.streaming)).toHaveLength(1);
    expect(s.entries.find((e) => e.streaming)?.delegationId).toBe("chat-2");
    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBe("chat-2");
  });
});

describe("finalize hardening: projection sanity for the settled flow", () => {
  it("a settled assistant projects message status complete + part status complete", () => {
    const projected = projectEntry({
      message: assistantMessage("a1", "**bold** and\n\n- list", "chat-1"),
      streaming: false,
    });
    expect((projected as { status?: unknown }).status).toEqual({
      type: "complete",
      reason: "stop",
    });
    const part = (projected?.content as unknown as { status?: { type: string } }[])[0];
    expect(part.status?.type).toBe("complete");
  });
});

// ---------------------------------------------------------------------------
// Hook-level: the composer gate survives the crash path end-to-end
// ---------------------------------------------------------------------------

type Handler = (env: WSEnvelope) => void;
const handlers = new Map<string, Set<Handler>>();
const subscribe = vi.fn((event: string, handler: Handler) => {
  let set = handlers.get(event);
  if (!set) {
    set = new Set();
    handlers.set(event, set);
  }
  set.add(handler);
  return () => {
    set.delete(handler);
  };
});

vi.mock("@/context/WSProvider", () => ({
  useWS: () => ({ state: "open", subscribe }),
}));

function emit(event: string, data: unknown) {
  const set = handlers.get(event);
  if (set) {
    const env = { event, data, timestamp: "" } as WSEnvelope;
    for (const h of [...set]) h(env);
  }
}

vi.mock("@/api/client", () => ({
  api: {
    getSession: vi.fn(),
    getActiveTurn: vi.fn(),
    sendMessage: vi.fn(),
    rerunTurn: vi.fn(),
  },
}));

const getSessionMock = vi.mocked(api.getSession);
const getActiveTurnMock = vi.mocked(api.getActiveTurn);

function sessionDetail(messages: SessionMessage[] = []): SessionDetail {
  return {
    id: "s-1",
    name: "S",
    project_name: "p",
    created_at: "2026-09-11T00:00:00Z",
    updated_at: "2026-09-11T00:00:00Z",
    orchestrator_session_id: null,
    status: "active",
    memory_bank: "",
    child_count: 0,
    message_count: messages.length,
    active: true,
    messages,
    children: [],
  };
}

describe("useSweaveChatRuntime: crash-path finalize (no message.added)", () => {
  beforeEach(() => {
    handlers.clear();
    subscribe.mockClear();
    getSessionMock.mockReset();
    getActiveTurnMock.mockReset();
    getSessionMock.mockResolvedValue(sessionDetail());
    getActiveTurnMock.mockResolvedValue(null);
  });

  it("status_changed(failed) with no message.added settles the bubble + re-enables the composer", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const utils = renderHook(() => useSweaveChatRuntime("s-1"), { wrapper });
    await waitFor(() => expect(getActiveTurnMock).toHaveBeenCalledWith("s-1"));
    await waitFor(() => expect(utils.result.current.turn).toBe("idle"));

    // A streaming turn is underway (status_changed running + deltas).
    act(() => {
      emit("delegation.status_changed", {
        session_id: "s-1",
        delegation_id: "chat-crash",
        status: "running",
        kind: "chat",
      });
      emit("chat.delta", {
        session_id: "s-1",
        delegation_id: "chat-crash",
        text: "partial **markdown",
      });
    });
    await waitFor(() => expect(utils.result.current.isRunning).toBe(true));
    expect(
      utils.result.current.messages.some(
        (m) => (m as unknown as { id: string }).id === "stream-chat-crash",
      ),
    ).toBe(true);

    // The turn CRASHES server-side: only status_changed(failed) arrives
    // (loop.py _crash_finalise emits no message.added).
    act(() => {
      emit("delegation.status_changed", {
        session_id: "s-1",
        delegation_id: "chat-crash",
        status: "failed",
        kind: "chat",
      });
    });

    expect(utils.result.current.turn).toBe("idle");
    expect(utils.result.current.isRunning).toBe(false);
    const projected = utils.result.current.messages;
    const like = projected.find(
      (m) => (m as unknown as { id: string }).id === "stream-chat-crash",
    ) as unknown as { status: { type: string }; content: { status?: { type: string } }[] };
    expect(like.status.type).toBe("complete");
    expect(like.content[0].status?.type).toBe("complete");
  });

  it("a late chat.delta after finalize does not re-stick the composer", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const utils = renderHook(() => useSweaveChatRuntime("s-1"), { wrapper });
    await waitFor(() => expect(getActiveTurnMock).toHaveBeenCalledWith("s-1"));
    await waitFor(() => expect(utils.result.current.turn).toBe("idle"));

    act(() => {
      emit("chat.delta", {
        session_id: "s-1",
        delegation_id: "chat-late",
        text: "streaming **tail",
      });
      emit("message.added", {
        session_id: "s-1",
        message: {
          id: "a9",
          role: "assistant",
          content: "full **answer**",
          timestamp: "2026-09-11T00:00:01Z",
          agent: "orchestrator",
          tool_name: null,
          tool_result: null,
          metadata: { delegation_id: "chat-late" },
        },
      });
    });
    await waitFor(() => expect(utils.result.current.turn).toBe("idle"));

    // Trailing delta AFTER finalize (WS reorder / replay).
    act(() => {
      emit("chat.delta", {
        session_id: "s-1",
        delegation_id: "chat-late",
        text: " zombie",
      });
    });

    expect(utils.result.current.turn).toBe("idle");
    expect(utils.result.current.isRunning).toBe(false);
    const assistants = utils.result.current.messages.filter(
      (m) => (m as unknown as { role: string }).role === "assistant",
    );
    expect(assistants).toHaveLength(1);
    const content = (assistants[0] as unknown as {
      content: { text: string }[];
    }).content;
    expect(content[0].text).toBe("full **answer**");
  });
});
