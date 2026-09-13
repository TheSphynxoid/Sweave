/**
 * TurnDelegations tests (chat-transparency slice).
 *
 * Pins: null when the turn has no children (childless turns render
 * byte-identical to before); one card per child in chronological
 * order with the LiveTree pill convention; expand reveals the output
 * summary (error text on failure); "Open full detail" mounts the
 * shared M1.9 DetailView modal; a `delegation.status_changed` WS
 * event refetches the list (the live pulse).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TurnDelegations } from "@/components/thread/TurnDelegations";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import type { Delegation } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    listDelegations: vi.fn(),
    getDelegationDetail: vi.fn(),
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

function fireStatusChanged() {
  for (const h of handlers.get("delegation.status_changed") ?? []) h();
}

function fireEscalated() {
  for (const h of handlers.get("specialist.escalated") ?? []) h();
}

function child(overrides: Partial<Delegation> & { delegation_id: string }): Delegation {
  return {
    schema_version: 5,
    task_id: `task-${overrides.delegation_id}`,
    agent: "backend",
    model: "opencode/glm-5.3",
    task: "Implement the orders endpoint with validation and tests",
    status: "running",
    created_at: "2026-09-09T10:00:00",
    updated_at: "2026-09-09T10:01:00",
    started_at: "2026-09-09T10:00:05",
    completed_at: null,
    parent_session_id: "ses-1",
    project_name: "shop",
    output: "",
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
    needs_attention: false,
    ...overrides,
  };
}

const listMock = vi.mocked(api.listDelegations);
const detailMock = vi.mocked(api.getDelegationDetail);

beforeEach(() => {
  vi.clearAllMocks();
  handlers.clear();
});

/** The app root provides the query client; tests supply a fresh one. */
function renderTurn(parentDelegationId: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TurnDelegations parentDelegationId={parentDelegationId} />
    </QueryClientProvider>,
  );
}

describe("TurnDelegations", () => {
  it("renders nothing when the turn has no children", async () => {
    listMock.mockResolvedValue([]);
    renderTurn("chat-abc");
    await waitFor(() => expect(listMock).toHaveBeenCalledWith({ parent_task_id: "chat-abc" }));
    expect(screen.queryByTestId("turn-delegations")).toBeNull();
  });

  it("renders one card per child, oldest first, with pills", async () => {
    listMock.mockResolvedValue([
      child({ delegation_id: "d-new", created_at: "2026-09-09T10:05:00", status: "running" }),
      child({
        delegation_id: "d-old",
        agent: "frontend",
        created_at: "2026-09-09T10:02:00",
        status: "done",
        output: "Cart page wired to the orders endpoint.",
      }),
    ]);
    renderTurn("chat-abc");
    const cards = await screen.findAllByTestId("turn-delegation-card");
    expect(cards).toHaveLength(2);
    // Chronological despite newest-first input.
    expect(cards[0].getAttribute("data-delegation-id")).toBe("d-old");
    expect(cards[1].getAttribute("data-delegation-id")).toBe("d-new");
    expect(screen.getByTestId("status-pill-running")).toBeTruthy();
    expect(screen.getByTestId("status-pill-done")).toBeTruthy();
    expect(screen.getByText("backend")).toBeTruthy();
    expect(screen.getByText("frontend")).toBeTruthy();
  });

  it("expand reveals the output summary; failures show the error", async () => {
    listMock.mockResolvedValue([
      child({ delegation_id: "d-ok", status: "done", output: "All 12 tests pass." }),
    ]);
    renderTurn("chat-abc");
    const cards = await screen.findAllByTestId("turn-delegation-card");
    fireEvent.click(cards[0].querySelector("button")!);
    expect(screen.getByText("All 12 tests pass.")).toBeTruthy();
  });

  it("a non-timeout failure keeps the red failed treatment", async () => {
    listMock.mockResolvedValue([
      child({
        delegation_id: "d-broken",
        status: "failed",
        error: "harness exited code 1",
      }),
    ]);
    renderTurn("chat-abc");
    const card = await screen.findByTestId("turn-delegation-card");
    fireEvent.click(card.querySelector("button")!);
    const pill = screen.getByTestId("status-pill-failed");
    expect(pill.textContent).toBe("Failed");
    expect(pill.className).toContain("rose");
    expect(screen.getByText("harness exited code 1")).toBeTruthy();
    expect(screen.queryByTestId("turn-delegation-timeout")).toBeNull();
  });

  it("a timed-out child renders the calm amber taxonomy, not the red failure", async () => {
    listMock.mockResolvedValue([
      child({
        delegation_id: "d-slow",
        status: "failed",
        error: "turn_timeout_exceeded_900s",
      }),
    ]);
    renderTurn("chat-abc");
    const card = await screen.findByTestId("turn-delegation-card");
    const pill = screen.getByTestId("status-pill-timed-out");
    expect(pill.textContent).toBe("⏱ Timed out (after 15 min)");
    expect(pill.className).toContain("amber");
    fireEvent.click(card.querySelector("button")!);
    expect(screen.getByTestId("turn-delegation-timeout").textContent).toContain(
      "ran out of its turn budget",
    );
    // "State at timeout": nothing persisted → say so explicitly.
    expect(screen.getByText("No output was persisted before the timeout.")).toBeTruthy();
    // The raw sentinel stays as an audit line.
    expect(screen.getByTestId("turn-delegation-timeout-raw").textContent).toBe(
      "turn_timeout_exceeded_900s",
    );
    expect(screen.queryByTestId("status-pill-failed")).toBeNull();
  });

  it("a timed-out child with partial output shows it as its state at timeout", async () => {
    listMock.mockResolvedValue([
      child({
        delegation_id: "d-slow2",
        status: "failed",
        error: "turn_timeout_exceeded_900s",
        output: "Wrote orders.py and 3 of 5 tests before the cut.",
      }),
    ]);
    renderTurn("chat-abc");
    const card = await screen.findByTestId("turn-delegation-card");
    fireEvent.click(card.querySelector("button")!);
    expect(screen.getByTestId("turn-delegation-timeout")).toBeTruthy();
    expect(
      screen.getByText("Wrote orders.py and 3 of 5 tests before the cut."),
    ).toBeTruthy();
    expect(
      screen.queryByText("No output was persisted before the timeout."),
    ).toBeNull();
  });

  it("a completed card shows its elapsed runtime when timestamps exist", async () => {
    listMock.mockResolvedValue([
      child({
        delegation_id: "d-done",
        status: "done",
        output: "Shipped.",
        started_at: "2026-09-09T10:00:00",
        completed_at: "2026-09-09T10:07:30",
      }),
    ]);
    renderTurn("chat-abc");
    const card = await screen.findByTestId("turn-delegation-card");
    fireEvent.click(card.querySelector("button")!);
    expect(screen.getByTestId("status-runtime").textContent).toBe("7 min");
    expect(screen.getByTestId("turn-delegation-runtime").textContent).toContain("Ran for 7 min");
  });

  it("a completed card without timestamps shows no runtime", async () => {
    listMock.mockResolvedValue([
      child({
        delegation_id: "d-done2",
        status: "done",
        output: "Shipped.",
        started_at: null,
        completed_at: "2026-09-09T10:07:30",
      }),
    ]);
    renderTurn("chat-abc");
    await screen.findByTestId("turn-delegation-card");
    expect(screen.queryByTestId("status-runtime")).toBeNull();
  });

  it("flags cards needing input and opens the shared detail modal", async () => {
    listMock.mockResolvedValue([
      child({ delegation_id: "d-ask", status: "running", needs_attention: true }),
    ]);
    detailMock.mockResolvedValue({
      delegation_id: "d-ask",
      composed_prompt: null,
      tool_timeline: [],
      tokens: null,
      status_timeline: [],
    });
    renderTurn("chat-abc");
    await screen.findByTestId("turn-delegation-card");
    expect(screen.getByText("needs input")).toBeTruthy();
    fireEvent.click(screen.getByTestId("turn-delegation-card").querySelector("button")!);
    fireEvent.click(screen.getByText("Open full detail"));
    expect(await screen.findByTestId("detail-modal")).toBeTruthy();
    expect(detailMock).toHaveBeenCalledWith("d-ask");
  });

  it("refetches on delegation.status_changed (live pulse)", async () => {
    listMock.mockResolvedValue([]);
    renderTurn("chat-abc");
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1));
    expect(useWS).toBeTruthy();
    fireStatusChanged();
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2));
  });

  it("refetches on specialist.escalated (the ask can outlive the turn)", async () => {
    // M1.12 stuck-reviewer regression: a permission ask can arrive
    // minutes AFTER the parent turn settled, so the escalation event
    // must refetch the children independently of status changes.
    listMock.mockResolvedValue([]);
    renderTurn("chat-abc");
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1));
    fireEscalated();
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2));
  });

  it("pending escalation renders an inline ask card; answering posts the option", async () => {
    const escMock = vi.mocked(api.getEscalation);
    const answerMock = vi.mocked(api.answerEscalation);
    listMock.mockResolvedValue([
      child({ delegation_id: "d-ask", status: "running", needs_attention: true }),
    ]);
    escMock.mockResolvedValueOnce({
      escalation_id: "esc-1",
      delegation_id: "d-ask",
      question: "Permission required: opencode asks external_directory.",
      options: ["allow once", "always allow", "deny"],
      kind: "permission",
      audience: "human",
      status: "pending",
      created_at: "2026-09-10T23:27:54",
      deadline_at: null,
      answered_at: null,
      response: null,
    });
    answerMock.mockResolvedValueOnce({
      escalation_id: "esc-1",
      delegation_id: "d-ask",
      question: "Permission required: opencode asks external_directory.",
      options: ["allow once", "always allow", "deny"],
      kind: "permission",
      audience: "human",
      status: "answered",
      created_at: "2026-09-10T23:27:54",
      deadline_at: null,
      answered_at: "2026-09-10T23:40:00",
      response: "allow once",
    });
    escMock.mockResolvedValueOnce({
      escalation_id: "esc-1",
      delegation_id: "d-ask",
      question: "Permission required: opencode asks external_directory.",
      options: ["allow once", "always allow", "deny"],
      kind: "permission",
      audience: "human",
      status: "answered",
      created_at: "2026-09-10T23:27:54",
      deadline_at: null,
      answered_at: "2026-09-10T23:40:00",
      response: "allow once",
    });

    renderTurn("chat-abc");
    const card = await screen.findByTestId("turn-delegation-card");
    fireEvent.click(card.querySelector("button")!);

    const askCard = await screen.findByTestId("turn-delegation-escalation");
    expect(askCard.textContent).toContain("Permission required");
    const options = screen.getAllByTestId("turn-escalation-option");
    expect(options).toHaveLength(3);
    fireEvent.click(options[0]);
    await waitFor(() =>
      expect(answerMock).toHaveBeenCalledWith("d-ask", "allow once"),
    );
    // Answered: the buttons give way to the resolved record.
    await waitFor(() =>
      expect(screen.queryByTestId("turn-escalation-option")).toBeNull(),
    );
    expect(screen.getByTestId("turn-delegation-escalation").textContent).toContain(
      "answered",
    );
  });

  it("a failed fetch never breaks the thread", async () => {
    listMock.mockRejectedValue(new Error("boom"));
    renderTurn("chat-abc");
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(screen.queryByTestId("turn-delegations")).toBeNull();
  });
});
