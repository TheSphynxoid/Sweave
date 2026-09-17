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
    answerEscalationBatch: vi.fn(),
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
const answerBatchMock = vi.mocked(api.answerEscalationBatch);
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

  it("permission kind renders the card with the metadata detail line", async () => {
    getMock.mockResolvedValue(
      rec({
        kind: "permission",
        question:
          "Permission required: opencode asks external_directory for [\"C:\\Windows\\*\"]. Answer 'allow once' / 'always allow' / 'deny'.",
        options: ["allow once", "always allow", "deny"],
        metadata: {
          requestID: "per_01",
          permission: "external_directory",
          patterns: ["C:\\Windows\\*"],
          command: "cat C:\\Windows\\win.ini",
        },
      }) as EscalationRecord,
    );
    answerMock.mockResolvedValue(rec({ status: "answered", response: "allow once" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    const card = await screen.findByTestId("turn-question-card");
    expect(card.getAttribute("data-kind")).toBe("permission");
    expect(screen.getByText(/Permission required — the turn is waiting/)).toBeTruthy();
    expect(
      screen.getByText(/tool check: external_directory.*patterns: C:\\Windows\\\*/),
    ).toBeTruthy();
    expect(
      screen.getByText(/'always allow' grants exactly: C:\\Windows\\\*/),
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("turn-question-option-allow once"));
    await waitFor(() =>
      expect(answerMock).toHaveBeenCalledWith("chat-abc", "allow once"),
    );
  });

  it("permission still renders after ws.escalated event", async () => {
    getMock.mockResolvedValue(
      rec({ kind: "permission", options: ["allow once"] }) as EscalationRecord,
    );
    render(<TurnQuestions delegationId="chat-abc" />);
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("turn-question-card")).toBeTruthy();
  });
});

// ---- TOOL_CARDS Step 3: batched multi-question escalation -----------------

describe("TurnQuestions (batched)", () => {
  function batchRec(overrides: Partial<EscalationRecord> = {}): EscalationRecord {
    return rec({
      question: "unused-legacy-field",
      options: null,
      questions: [
        { question: "JWT or session?", options: ["JWT", "session"] },
        { question: "Cache strategy?", options: ["in-memory", "redis"] },
      ],
      answers: [null, null],
      ...overrides,
    });
  }

  it("renders stacked sections for every question in a batch", async () => {
    getMock.mockResolvedValue(batchRec());
    answerBatchMock.mockResolvedValue(batchRec({ status: "answered" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    const card = await screen.findByTestId("turn-question-card");
    expect(card.getAttribute("data-batch")).toBe("2");
    expect(await screen.findByTestId("turn-question-section-0")).toBeTruthy();
    expect(await screen.findByTestId("turn-question-section-1")).toBeTruthy();
    expect(screen.getByText("JWT or session?")).toBeTruthy();
    expect(screen.getByText("Cache strategy?")).toBeTruthy();
  });

  it("renders option buttons per question", async () => {
    getMock.mockResolvedValue(batchRec());
    answerBatchMock.mockResolvedValue(batchRec({ status: "answered" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    await screen.findByTestId("turn-question-card");
    expect(screen.getByTestId("turn-question-option-0-JWT")).toBeTruthy();
    expect(screen.getByTestId("turn-question-option-0-session")).toBeTruthy();
    expect(screen.getByTestId("turn-question-option-1-in-memory")).toBeTruthy();
    expect(screen.getByTestId("turn-question-option-1-redis")).toBeTruthy();
    // The legacy single-question option testid must NOT leak into batch.
    expect(screen.queryByTestId("turn-question-option-JWT")).toBeNull();
  });

  it("answering one option posts the full answers[] (that index set, others carried)", async () => {
    getMock.mockResolvedValue(batchRec({ answers: [null, "redis"] }));
    answerBatchMock.mockResolvedValue(batchRec({ status: "answered" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    await screen.findByTestId("turn-question-card");
    fireEvent.click(screen.getByTestId("turn-question-option-0-JWT"));
    await waitFor(() =>
      expect(answerBatchMock).toHaveBeenCalledWith("chat-abc", [
        "JWT",
        "redis",
      ]),
    );
  });

  it("Send-all posts the full answers[] from filled drafts", async () => {
    getMock.mockResolvedValue(batchRec());
    answerBatchMock.mockResolvedValue(batchRec({ status: "answered" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    await screen.findByTestId("turn-question-card");
    fireEvent.change(screen.getByTestId("turn-question-input-0"), {
      target: { value: "JWT" },
    });
    fireEvent.change(screen.getByTestId("turn-question-input-1"), {
      target: { value: "redis" },
    });
    fireEvent.click(screen.getByTestId("turn-question-send"));
    await waitFor(() =>
      expect(answerBatchMock).toHaveBeenCalledWith("chat-abc", [
        "JWT",
        "redis",
      ]),
    );
  });

  it("per-question answer button posts the full answers[] with that index set", async () => {
    getMock.mockResolvedValue(batchRec());
    answerBatchMock.mockResolvedValue(batchRec({ status: "answered" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    await screen.findByTestId("turn-question-card");
    fireEvent.change(screen.getByTestId("turn-question-input-1"), {
      target: { value: "redis" },
    });
    fireEvent.click(screen.getByTestId("turn-question-answer-1"));
    await waitFor(() =>
      expect(answerBatchMock).toHaveBeenCalledWith("chat-abc", [
        "",
        "redis",
      ]),
    );
  });

  it("legacy single-question record degrades to today's single card", async () => {
    // No `questions[]` → single card via the top-level fields.
    getMock.mockResolvedValue(rec({ options: ["JWT", "session"] }));
    answerMock.mockResolvedValue(rec({ status: "answered", response: "JWT" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    const card = await screen.findByTestId("turn-question-card");
    expect(card.getAttribute("data-batch")).toBeNull();
    expect(screen.getByTestId("turn-question-input")).toBeTruthy();
    expect(screen.getByTestId("turn-question-option-JWT")).toBeTruthy();
    fireEvent.click(screen.getByTestId("turn-question-option-JWT"));
    await waitFor(() =>
      expect(answerMock).toHaveBeenCalledWith("chat-abc", "JWT"),
    );
    // Batch API must NOT be touched by the legacy path.
    expect(answerBatchMock).not.toHaveBeenCalled();
  });

  it("skip posts once after a confirmed system dialog (whole batch)", async () => {
    getMock.mockResolvedValue(batchRec());
    skipMock.mockResolvedValue(batchRec({ status: "skipped" }));
    render(<TurnQuestions delegationId="chat-abc" />);
    await screen.findByTestId("turn-question-card");
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(false);
    fireEvent.click(screen.getByTestId("turn-question-skip"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(skipMock).not.toHaveBeenCalled();
    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByTestId("turn-question-skip"));
    await waitFor(() => expect(skipMock).toHaveBeenCalledWith("chat-abc"));
    expect(skipMock).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();
  });
});
