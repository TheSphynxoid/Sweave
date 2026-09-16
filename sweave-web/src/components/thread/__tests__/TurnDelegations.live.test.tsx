/**
 * TurnDelegations live-poll integration (4c-frontend, ruling Q3).
 *
 * Pins: while a child delegation is running/queued the component
 * re-fetches its children list on a ~4s cadence; once the last child
 * settles the polling STOPS (no idle refetch). The three standing WS
 * events still refetch on transition. Uses fake timers + act so the
 * cadence is deterministic without a real server.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TurnDelegations } from "@/components/thread/TurnDelegations";
import { api } from "@/api/client";
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

const listMock = vi.mocked(api.listDelegations);
const LIVE_POLL_MS = 4000;

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

function child(status: Delegation["status"]): Delegation {
  return {
    schema_version: 5,
    delegation_id: `d-${status}`,
    task_id: "t",
    agent: "backend",
    model: "m",
    task: "task",
    status,
    created_at: "2026-09-09T10:00:00",
    updated_at: "2026-09-09T10:00:00",
    started_at: status === "queued" ? null : "2026-09-09T10:00:00",
    completed_at: status === "done" ? "2026-09-09T10:05:00" : null,
    parent_session_id: "chat-abc",
    project_name: null,
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
  };
}

function renderTurn() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TurnDelegations parentDelegationId="chat-abc" />
    </QueryClientProvider>,
  );
}

describe("TurnDelegations live poll", () => {
  it("polls while a child is running and stops once settled", async () => {
    // Mount returns a running child; the FIRST poll tick returns a
    // settled child, which must halt polling thereafter.
    let call = 0;
    listMock.mockImplementation(async () => {
      call += 1;
      return [child(call === 1 ? "running" : "done")];
    });

    await act(async () => {
      renderTurn();
    });
    expect(listMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("turn-delegations")).toBeTruthy();

    // One poll tick while running -> a 2nd fetch.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_POLL_MS);
    });
    expect(listMock).toHaveBeenCalledTimes(2);

    // Now settled; further ticks must NOT refetch.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 3);
    });
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("does not poll at all when the turn has only settled children", async () => {
    listMock.mockResolvedValue([child("done")]);
    await act(async () => {
      renderTurn();
    });
    expect(listMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 3);
    });
    // No idle polling.
    expect(listMock).toHaveBeenCalledTimes(1);
  });
});
