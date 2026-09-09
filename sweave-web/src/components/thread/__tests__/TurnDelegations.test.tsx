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
      child({ delegation_id: "d-bad", status: "failed", error: "turn_timeout_exceeded_900s" }),
    ]);
    renderTurn("chat-abc");
    const cards = await screen.findAllByTestId("turn-delegation-card");
    fireEvent.click(cards[0].querySelector("button")!);
    expect(screen.getByText("All 12 tests pass.")).toBeTruthy();
    fireEvent.click(cards[1].querySelector("button")!);
    expect(screen.getByText("turn_timeout_exceeded_900s")).toBeTruthy();
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
    expect(screen.getByText("• needs input")).toBeTruthy();
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

  it("a failed fetch never breaks the thread", async () => {
    listMock.mockRejectedValue(new Error("boom"));
    renderTurn("chat-abc");
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(screen.queryByTestId("turn-delegations")).toBeNull();
  });
});
