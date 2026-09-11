/**
 * Hook-level turn-recovery tests (2026-09-10 recovery contract).
 *
 * The pure snapshot reducer (``applyTurnSnapshot``) is pinned in
 * runtime.test.ts; here we pin the HOOK's server-truth behaviors:
 *   (a) page load / session activation with an ACTIVE turn ->
 *       waiting/streaming indicator restored (turn running,
 *       partial text recovered), composer disabled (isRunning).
 *   (b) the turn's completion event (WS message.added) clears it
 *       and re-enables the composer.
 *   (c) sending while a turn is active (the user raced a refresh)
 *       receives HTTP 409 whose body carries the snapshot -> the
 *       hook ADOPTS the snapshot (streaming view + block) and does
 *       NOT error.
 *   (d) idle (the endpoint returns no active turn) -> no change:
 *       empty local in-progress state, composer enabled.
 * Plus the WS-reconnect re-eye: every transition back to "open"
 * re-fetches the snapshot.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useSweaveChatRuntime } from "../useSweaveChatRuntime";
import { api } from "@/api/client";
import type { SessionDetail, SessionMessage, TurnSnapshot, WSEnvelope } from "@/types";
import type { AppendMessage } from "@assistant-ui/react";

vi.mock("@/api/client", () => ({
  api: {
    getSession: vi.fn(),
    getActiveTurn: vi.fn(),
    sendMessage: vi.fn(),
    rerunTurn: vi.fn(),
  },
}));

// --- Fake WS: module-level state the tests mutate ----------------------

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
  useWS: () => ({ state: (globalThis as { __wsState?: string }).__wsState ?? "open", subscribe }),
}));

function setWsState(state: string) {
  (globalThis as { __wsState?: string }).__wsState = state;
}

function emit(event: string, data: unknown) {
  const set = handlers.get(event);
  if (set) {
    const env = { event, data, timestamp: "" } as WSEnvelope;
    for (const h of [...set]) h(env);
  }
}

const getSessionMock = vi.mocked(api.getSession);
const getActiveTurnMock = vi.mocked(api.getActiveTurn);
const sendMessageMock = vi.mocked(api.sendMessage);

function sessionDetail(messages: SessionMessage[] = []): SessionDetail {
  return {
    id: "s-1",
    name: "S",
    project_name: "p",
    created_at: "2026-09-10T00:00:00Z",
    updated_at: "2026-09-10T00:00:00Z",
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

function snapshot(overrides: Partial<TurnSnapshot> = {}): TurnSnapshot {
  return {
    session_id: "s-1",
    delegation_id: "chat-active",
    status: "running",
    phase: "streaming",
    started_at: "2026-09-10T12:00:00Z",
    stream_text: "Recovered partial",
    thinking_text: "",
    pending_question: false,
    ...overrides,
  };
}

function sendMessageError(
  status: number,
  body: unknown,
): unknown {
  // AxiosError-shaped enough for `axios.isAxiosError` + our reader.
  const err = Object.assign(new Error("Request failed"), {
    isAxiosError: true,
    response: { status, data: body },
  });
  return err;
}

function mkWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

async function renderChat(sessionId: string | null = "s-1") {
  const wrapper = mkWrapper();
  const utils = renderHook(() => useSweaveChatRuntime(sessionId), { wrapper });
  const res = () => utils.result.current;
  // Wait until the initial fold settled: history loaded (+ the
  // recovery re-eye fired) — otherwise a fold completing mid-test
  // replaces the whole state (server truth resets local in-progress).
  await waitFor(() => expect(getActiveTurnMock).toHaveBeenCalledWith(sessionId));
  if (sessionId === null) return { utils, res };
  await waitFor(() =>
    expect(getSessionMock).toHaveBeenCalledWith(sessionId),
  );
  return { utils, res };
}

beforeEach(() => {
  handlers.clear();
  subscribe.mockClear();
  setWsState("open");
  getSessionMock.mockReset();
  getActiveTurnMock.mockReset();
  sendMessageMock.mockReset();
  getSessionMock.mockResolvedValue(sessionDetail());
  getActiveTurnMock.mockResolvedValue(null);
  sendMessageMock.mockResolvedValue({
    success: true,
    assistant: {} as SessionMessage,
  });
});

describe("useSweaveChatRuntime: turn recovery (2026-09-10 contract)", () => {
  it("(a) page load with an ACTIVE turn: indicator restored, composer disabled, text recovered", async () => {
    getActiveTurnMock.mockResolvedValue(snapshot());

    const { res } = await renderChat();

    await waitFor(() => expect(res().isRunning).toBe(true));
    expect(res().turn).toBe("running");
    // The recovered partial text is in the thread as a streaming bubble.
    const messages = res().messages;
    const streaming = messages.find(
      (m) => m.id === "stream-chat-active",
    ) as unknown as { content: { text: string }[]; status: { type: string } };
    expect(streaming).toBeDefined();
    expect(streaming.content[0].text).toBe("Recovered partial");
    expect(streaming.status.type).toBe("running");
  });

  it("(b) the completion event clears the restored turn and re-enables the composer", async () => {
    getActiveTurnMock.mockResolvedValue(snapshot());
    const { res } = await renderChat();
    // Stabilize: the recovered streaming bubble must exist before the
    // completion event (the in-flight snapshot promise resolves inside
    // the next act otherwise — emit then re-apply races).
    await waitFor(() =>
      expect(res().messages.some((m) => m.id === "stream-chat-active")).toBe(true),
    );

    // Emit the RAW event data: emit() wraps it into the WSEnvelope the
    // hook consumes (env.data must BE the payload, not an envelope).
    act(() => {
      emit("message.added", {
        session_id: "s-1",
        message: {
          id: "a1",
          role: "assistant",
          content: "Final reply",
          timestamp: "2026-09-10T12:01:00Z",
          agent: "orchestrator",
          tool_name: null,
          tool_result: null,
          metadata: { delegation_id: "chat-active" },
        },
      });
    });

    expect(res().turn).toBe("idle");
    expect(res().isRunning).toBe(false);
    const messages = res().messages;
    const final = messages.find((m) => m.id === "a1") as unknown as {
      content: { text: string }[];
    };
    expect(final.content[0].text).toBe("Final reply");
    expect(messages.find((m) => m.id === "stream-chat-active")).toBeUndefined();
  });

  it("(c) send while a turn is active gets 409 -> adopts the snapshot, no error", async () => {
    // The turn started from a DIFFERENT surface (refreshed page after
    // the POST left): the hook starts idle.
    const { res } = await renderChat();
    expect(res().turn).toBe("idle");

    sendMessageMock.mockRejectedValue(
      sendMessageError(409, {
        detail: { error: "turn_active", message: "turn active", turn: snapshot() },
      }),
    );

    await act(async () => {
      await res().onNew({
        id: "local-1",
        parentId: null,
        createdAt: new Date(),
        content: [{ type: "text", text: "second send" }],
        role: "user",
      } as unknown as AppendMessage);
    });

    // Adopted: running + the recovered partial text, composer blocked.
    await waitFor(() => expect(res().turn).toBe("running"));
    expect(res().isRunning).toBe(true);
    const streaming = res().messages.find(
      (m) => m.id === "stream-chat-active",
    ) as unknown as { content: { text: string }[] };
    expect(streaming.content[0].text).toBe("Recovered partial");
  });

  it("(d) idle (no active turn): no change", async () => {
    getActiveTurnMock.mockResolvedValue(null);

    const { res } = await renderChat();

    expect(res().turn).toBe("idle");
    expect(res().isRunning).toBe(false);
    expect(getActiveTurnMock).toHaveBeenCalledWith("s-1");
  });

  it("reconnect re-eyes the snapshot (waiting indicator restored after socket drop)", async () => {
    // First open is IDLE: no active turn yet.
    getActiveTurnMock.mockResolvedValue(null);
    const { utils, res } = await renderChat();
    await waitFor(() => expect(res().turn === "idle").toBe(true));

    // Socket drops and comes back while a turn is running server-side.
    act(() => {
      setWsState("reconnecting");
    });
    utils.rerender();
    getActiveTurnMock.mockClear();
    getActiveTurnMock.mockResolvedValue(snapshot());
    // The mocked WS module-level state only re-reads on re-render;
    // the hook must observe the reconnecting→open transition.
    act(() => {
      setWsState("open");
    });
    utils.rerender();
    await waitFor(() => expect(getActiveTurnMock).toHaveBeenCalled());
    await waitFor(() => expect(res().isRunning).toBe(true));
    expect(getActiveTurnMock).toHaveBeenCalledWith("s-1");
  });
});
