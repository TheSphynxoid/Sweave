/**
 * TurnQuestions tests (M1.11 blocking Q&A).
 *
 * Pins: null when no escalation / resolved / non-question kind;
 * pending question renders the card with options + input; answer
 * posts and reloads; skip runs the system confirm first (cancel =
 * no POST); WS escalated/resolved events reload.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TurnQuestions } from "@/components/thread/TurnQuestions";
import { api } from "@/api/client";
import type { EscalationRecord } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    getEscalation: vi.fn(),
    answerEscalation: vi.fn(),
    skipEscalation: vi.fn(),
  },
}));

const handlers = new Map<string, Array<() => void>>();
vi.mock("@/context/WSProvider", () => ({
  useWS: () => ({
    state: "open",
    subscribe: (event: string, handler: () => void) => {
      const list = handlers.get(event) ?? [];
      list.push(handler);
      handlers.set(event, list);
      return () => {
        handlers.set(
          event,
          (handlers.get(event) ?? []).filter((h) => h !== handler),
        );
      };
    },
  }),
}));

const getMock = vi.mocked(api.getEscalation);
const answerMock = vi.mocked(api.answerEscalation);
const skipMock = vi.mocked(api.skipEscalation);

function rec(overrides: Partial<EscalationRecord> = {}): EscalationRecord {
  return {
    escalation_id: "esc-1",
    delegation_id: "chat-abc",
    question: "JWT or session?",
    options: null,
    kind: "question",
    audience: "human",
    status: "pending",
    created_at: "2026-09-10T10:00:00",
    deadline_at: null,
    answered_at: null,
    response: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  handlers.clear();
});

describe("TurnQuestions", () => {
  it("renders nothing when there is no escalation", async () => {
    getMock.mockResolvedValue(null);
    render(<TurnQuestions delegationId="chat-abc" />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("chat-abc"));
    expect(screen.queryByTestId("turn-question-card")).toBeNull();
  });

  it("renders nothing once resolved or for non-question kinds", async () => {
    getMock.mockResolvedValue(rec({ status: "answered", response: "JWT" }));
    const { unmount } = render(<TurnQuestions delegationId="chat-abc" />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(screen.queryByTestId("turn-question-card")).toBeNull();
    unmount();
    getMock.mockResolvedValue(rec({ kind: "escalation", audience: "orchestrator" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("turn-question-card")).toBeNull();
  });

  it("pending question renders options, answers, and skips via system confirm", async () => {
    getMock.mockResolvedValue(rec({ options: ["JWT", "session"] }));
    answerMock.mockResolvedValue(rec({ status: "answered" }));
    skipMock.mockResolvedValue(rec({ status: "skipped" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    await screen.findByTestId("turn-question-card");
    expect(screen.getByText("JWT or session?")).toBeTruthy();
    fireEvent.click(screen.getByTestId("turn-question-option-JWT"));
    await waitFor(() =>
      expect(answerMock).toHaveBeenCalledWith("chat-abc", "JWT"),
    );

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByTestId("turn-question-skip"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(skipMock).not.toHaveBeenCalled();
    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByTestId("turn-question-skip"));
    await waitFor(() => expect(skipMock).toHaveBeenCalledWith("chat-abc"));
    confirmSpy.mockRestore();
  });

  it("reloads on escalation WS events", async () => {
    getMock.mockResolvedValue(null);
    render(<TurnQuestions delegationId="chat-abc" />);
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    for (const h of handlers.get("specialist.escalated") ?? []) h();
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    for (const h of handlers.get("specialist.escalation_resolved") ?? []) h();
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(3));
  });
});
