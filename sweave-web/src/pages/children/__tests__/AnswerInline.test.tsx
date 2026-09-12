/**
 * AnswerInline gating (M2.2 fix, 2026-09-12).
 *
 * ``needs_attention`` means "answer OR promote": a review-only row
 * carries the flag with NO escalation record. Pins:
 * - review + flag + no escalation (GET → null) → NO Answer button,
 *   but Mark done still renders;
 * - flag + pending escalation → Answer button renders and answering
 *   posts the response;
 * - flag + resolved escalation (review still owed) → NO Answer
 *   button (the remaining action is promotion);
 * - late-arriving question (404 first, then `specialist.escalated`)
 *   → the Answer button lights up without a list refetch.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LiveTree } from "../LiveTree";
import { api } from "@/api/client";
import type { Delegation, EscalationRecord } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    getEscalation: vi.fn(),
    answerEscalation: vi.fn(),
    skipEscalation: vi.fn(),
    promoteDelegation: vi.fn(),
  },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({ pushNotification }),
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

function fireEscalated() {
  for (const h of handlers.get("specialist.escalated") ?? []) h();
}

function fireResolved() {
  for (const h of handlers.get("specialist.escalation_resolved") ?? []) h();
}

function delegation(overrides: Partial<Delegation>): Delegation {
  return {
    schema_version: 9,
    delegation_id: "d-1",
    task_id: "task-1",
    agent: "reviewer-specialist",
    model: "opencode/muse-spark-1.3-contributor-free",
    task: "check the plan and state",
    status: "review",
    created_at: "2026-09-12T21:50:00",
    updated_at: "2026-09-12T21:52:00",
    started_at: "2026-09-12T21:50:05",
    completed_at: null,
    parent_session_id: null,
    project_name: "Sweave",
    output: "report text",
    error: null,
    worktree_path: null,
    branch: null,
    pr_url: null,
    parent_task_id: "chat-abc",
    manifest: null,
    depth: 1,
    chain_root_id: "chat-abc",
    coordination_tokens: 0,
    kind: "task",
    needs_attention: true,
    ...overrides,
  };
}

function pendingQuestion(): EscalationRecord {
  return {
    escalation_id: "esc-1",
    delegation_id: "d-1",
    question: "Proceed?",
    options: null,
    kind: "question",
    audience: "human",
    status: "pending",
    created_at: "2026-09-12T21:52:00",
    deadline_at: null,
    answered_at: null,
    response: null,
  };
}

const getMock = vi.mocked(api.getEscalation);
const answerMock = vi.mocked(api.answerEscalation);

function renderTree(rows: Delegation[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LiveTree delegations={rows} onOpen={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  handlers.clear();
});

describe("AnswerInline gating", () => {
  it("review-only flag (no escalation) shows Mark done but no Answer", async () => {
    getMock.mockResolvedValue(null);
    renderTree([delegation({})]);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("d-1"));
    // The review gate stays actionable…
    expect(screen.getByTestId("promote-d-1")).toBeTruthy();
    // …but there is no question to answer, so no dead Answer button.
    await waitFor(() =>
      expect(screen.queryByTestId("answer-toggle-d-1")).toBeNull(),
    );
  });

  it("pending escalation shows Answer and answering posts the response", async () => {
    getMock.mockResolvedValue(pendingQuestion());
    answerMock.mockResolvedValue({ ...pendingQuestion(), status: "answered" });
    renderTree([delegation({})]);
    // The flagged row mounts twice (tree row + top escalation lane).
    const toggles = await screen.findAllByTestId("answer-toggle-d-1");
    expect(toggles.length).toBe(2);
    fireEvent.click(toggles[0]);
    const input = screen.getAllByTestId("answer-input-d-1")[0];
    fireEvent.change(input, { target: { value: "yes, proceed" } });
    // After the answer lands the refetch sees a resolved record while
    // the review is still owed → both buttons go away (promote remains).
    getMock.mockResolvedValue({ ...pendingQuestion(), status: "answered" });
    fireEvent.click(screen.getAllByTestId("answer-send-d-1")[0]);
    await waitFor(() =>
      expect(answerMock).toHaveBeenCalledWith("d-1", "yes, proceed"),
    );
    // The server publishes specialist.escalation_resolved on answer;
    // both mounted instances (tree row + lane) refetch and hide.
    fireResolved();
    await waitFor(() =>
      expect(screen.queryAllByTestId("answer-toggle-d-1")).toHaveLength(0),
    );
    expect(screen.getByTestId("promote-d-1")).toBeTruthy();
  });

  it("resolved escalation with review still owed shows no Answer", async () => {
    getMock.mockResolvedValue({ ...pendingQuestion(), status: "answered" });
    renderTree([delegation({})]);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("d-1"));
    await waitFor(() =>
      expect(screen.queryByTestId("answer-toggle-d-1")).toBeNull(),
    );
    expect(screen.getByTestId("promote-d-1")).toBeTruthy();
  });

  it("late-arriving question lights the Answer button up via WS", async () => {
    getMock.mockResolvedValue(null);
    renderTree([delegation({})]);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("d-1"));
    expect(screen.queryByTestId("answer-toggle-d-1")).toBeNull();
    // The permission ask lands minutes after the row mounted.
    getMock.mockResolvedValue(pendingQuestion());
    fireEscalated();
    await screen.findAllByTestId("answer-toggle-d-1");
  });
});
