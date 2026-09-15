/**
 * Stats page (usage ledger surface).
 *
 * Pins: totals cards, per-day/per-model splits, failure classes, and
 * the empty state — all from one `getStatsSummary` call (computed on
 * read, counts only, never message text).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StatsPage } from "../Stats";
import { api } from "@/api/client";
import type { StatsSummary } from "@/types";

vi.mock("@/api/client", () => ({
  api: { getStatsSummary: vi.fn() },
}));

vi.mock("@/context/WSProvider", () => ({
  useWS: () => ({ subscribe: () => () => {}, state: "open" as const }),
}));

const summaryMock = vi.mocked(api.getStatsSummary);

function summary(): StatsSummary {
  const cell = {
    turns: 0,
    input: 0,
    output: 0,
    reasoning: 0,
    cache_read: 0,
    cache_write: 0,
    cost: 0,
    context_input: 0,
    failed: 0,
    estimated_cost: 0,
    cost_source: "none" as const,
    unpriced: true,
  };
  return {
    window_days: 30,
    generated_at: "2026-09-14T00:00:00",
    totals: { ...cell, turns: 3, input: 1500, output: 150, failed: 1, wall_seconds: 300, completed_turns: 2 },
    by_day: [{ day: "2026-09-14", ...cell, turns: 3, input: 1500, output: 150, failed: 1 }],
    by_model: [{ model: "opencode/m", ...cell, turns: 3, input: 1500 }],
    by_project: [{ project: "demo", ...cell, turns: 3 }],
    by_agent: [{ agent: "backend", ...cell, turns: 3 }],
    by_kind: [{ kind: "task", ...cell, turns: 3 }],
    by_status: { done: 2, failed: 1 },
    by_error: [{ error: "max_steps", count: 1 }],
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StatsPage />
    </QueryClientProvider>,
  );
}

describe("StatsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders totals, splits, and failure classes", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals")).toBeDefined());
    // Locale-independent: toLocaleString grouping varies by ICU ("1,500" vs "1 500").
    expect(screen.getByTestId("stats-totals").textContent).toContain("500");
    expect(screen.getByTestId("stats-totals").textContent).toContain("Peak context");
    expect(screen.getByTestId("stats-by-day").textContent).toContain("2026-09-14");
    expect(screen.getByTestId("stats-by-model").textContent).toContain("opencode/m");
    expect(screen.getByTestId("stats-errors").textContent).toContain("max_steps");
  });

  it("renders the empty state with no turns", async () => {
    summaryMock.mockResolvedValue({ ...summary(), totals: { ...summary().totals, turns: 0 } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("stats-page").textContent).toContain("No turns recorded"),
    );
  });

  it("fetches the default 30-day window", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(summaryMock).toHaveBeenCalled());
    expect(summaryMock).toHaveBeenCalledWith();
  });
});
