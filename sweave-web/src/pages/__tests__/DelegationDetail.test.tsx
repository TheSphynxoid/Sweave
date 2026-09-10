/**
 * DelegationDetail page tests — the honest-failure taxonomy on
 * the deep-link surface.
 *
 * Pins: timeout errors render the calm amber taxonomy (pill +
 * explanatory copy + state-at-timeout hint), non-timeout failures
 * keep the red treatment, completed delegations show elapsed
 * runtime when the timestamps exist.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { DelegationDetailPage } from "@/pages/DelegationDetail";
import { api } from "@/api/client";
import type { Delegation } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    getDelegation: vi.fn(),
    getDelegationDetail: vi.fn(),
  },
}));

function delegation(overrides: Partial<Delegation>): Delegation {
  return {
    schema_version: 5,
    delegation_id: "d-timeout-1",
    task_id: "task-1",
    agent: "backend",
    model: "opencode/glm-5.3",
    task: "Ship the orders endpoint",
    status: "failed",
    created_at: "2026-09-09T10:00:00",
    updated_at: "2026-09-09T10:16:00",
    started_at: "2026-09-09T10:00:05",
    completed_at: null,
    parent_session_id: "ses-1",
    project_name: "shop",
    output: "",
    error: null,
    worktree_path: null,
    branch: null,
    pr_url: null,
    parent_task_id: null,
    manifest: null,
    depth: 1,
    chain_root_id: null,
    coordination_tokens: 0,
    kind: "task",
    needs_attention: false,
    ...overrides,
  };
}

const getMock = vi.mocked(api.getDelegation);

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPage(id = "d-timeout-1") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[`/delegations/${id}`]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/delegations/:id" element={<DelegationDetailPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("DelegationDetailPage", () => {
  it("renders a timeout error with the calm amber taxonomy", async () => {
    getMock.mockResolvedValue(
      delegation({
        error: "turn_timeout_exceeded_900s",
        output: "Drafted orders.py before the cut.",
      }),
    );
    renderPage();
    const pill = await screen.findByTestId("delegation-status-pill");
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("d-timeout-1"));
    expect(pill.textContent).toContain("⏱ Timed out (after 15 min)");
    expect(await screen.findByTestId("delegation-timeout")).toBeTruthy();
    expect(pill.className).toContain("amber");
    expect(screen.getByTestId("delegation-timeout").textContent).toContain(
      "ran out of its turn budget",
    );
    expect(screen.getByTestId("delegation-timeout").textContent).toContain(
      "partial output may still be present",
    );
    expect(screen.getByTestId("delegation-state-at-timeout").textContent).toContain(
      "Drafted orders.py before the cut.",
    );
    expect(screen.queryByTestId("delegation-error")).toBeNull();
  });

  it("a timed-out delegation with no persisted output says so", async () => {
    getMock.mockResolvedValue(delegation({ error: "turn_timeout_exceeded_900s" }));
    renderPage();
    expect(await screen.findByTestId("delegation-timeout")).toBeTruthy();
    expect(screen.getByTestId("delegation-state-at-timeout").textContent).toContain(
      "No output was persisted before the timeout",
    );
  });

  it("a non-timeout failure keeps the red treatment and error text", async () => {
    getMock.mockResolvedValue(
      delegation({ error: "harness exited code 1", output: "" }),
    );
    renderPage();
    expect(await screen.findByTestId("delegation-status-pill")).toBeTruthy();
    const pill = screen.getByTestId("delegation-status-pill");
    expect(pill.className).toContain("rose");
    expect(screen.getByTestId("delegation-error").textContent).toContain(
      "harness exited code 1",
    );
    expect(screen.queryByTestId("delegation-timeout")).toBeNull();
  });

  it("a completed delegation shows its elapsed runtime", async () => {
    getMock.mockResolvedValue(
      delegation({
        status: "done",
        error: null,
        output: "All green.",
        started_at: "2026-09-09T10:00:00",
        completed_at: "2026-09-09T10:03:20",
      }),
    );
    renderPage();
    expect(await screen.findByTestId("delegation-status-runtime")).toBeTruthy();
    expect(screen.getByTestId("delegation-status-runtime").textContent).toContain(
      "3 min",    );
    expect(screen.queryByTestId("delegation-timeout")).toBeNull();
  });

  it("a completed delegation without timestamps shows no runtime", async () => {
    getMock.mockResolvedValue(
      delegation({ status: "done", error: null, output: "All green." }),
    );
    renderPage();
    await screen.findByTestId("delegation-status-pill");
    expect(screen.queryByTestId("delegation-status-runtime")).toBeNull();
  });
});
