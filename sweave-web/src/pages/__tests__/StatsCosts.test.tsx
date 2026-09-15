/**
 * Stats Phase 1b: cost column, sparkline, Free/unpriced states.
 *
 * Pins: `SplitTable` renders an Est. cost column (Free / $x / unpriced,
 * never $0); the per-day sparkline mounts with token line + cost bars;
 * the totals Est. cost card shows Free/unpriced states honestly.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StatsPage } from "../Stats";
import { api } from "@/api/client";
import type { StatsSummary, StatsCell } from "@/types";

vi.mock("@/api/client", () => ({
  api: { getStatsSummary: vi.fn() },
}));

vi.mock("@/context/WSProvider", () => ({
  useWS: () => ({ subscribe: () => () => {}, state: "open" as const }),
}));

const summaryMock = vi.mocked(api.getStatsSummary);

function pricedCell(over: Partial<StatsCell> = {}): StatsCell {
  return {
    turns: 1,
    input: 1_000_000,
    output: 0,
    reasoning: 0,
    cache_read: 0,
    cache_write: 0,
    cost: 0,
    context_input: 0,
    failed: 0,
    estimated_cost: 1.0,
    cost_source: "rates",
    unpriced: false,
    ...over,
  };
}

function summary(): StatsSummary {
  return {
    window_days: 30,
    generated_at: "2026-09-14T00:00:00",
    totals: {
      ...pricedCell({ turns: 2, input: 2_000_000, estimated_cost: 2.0 }),
      wall_seconds: 60,
      completed_turns: 2,
    },
    by_day: [
      { day: "2026-09-13", ...pricedCell() },
      { day: "2026-09-14", ...pricedCell({ estimated_cost: 0, cost_source: "none", unpriced: true }) },
    ],
    by_model: [{ model: "prov/m", ...pricedCell({ turns: 2, estimated_cost: 2.0 }) }],
    by_project: [{ project: "demo", ...pricedCell({ turns: 2, estimated_cost: 2.0 }) }],
    by_agent: [{ agent: "backend", ...pricedCell({ turns: 2, estimated_cost: 2.0 }) }],
    by_kind: [{ kind: "task", ...pricedCell({ turns: 2, estimated_cost: 2.0 }) }],
    by_status: { done: 2 },
    by_error: [],
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

describe("StatsCosts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the Est. cost column on split tables", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-by-day")).toBeDefined());
    expect(screen.getByTestId("stats-by-day").textContent).toContain("Est. cost");
    // Priced day renders $1.0000; unpriced day renders "unpriced", never $0.
    expect(screen.getByTestId("stats-by-day").textContent).toContain("$1.0000");
    expect(screen.getByTestId("stats-by-day").textContent).toContain("unpriced");
    expect(screen.getByTestId("stats-by-day").textContent).not.toContain("$0.0000");
  });

  it("renders the per-day sparkline with token line + cost bars", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-sparkline")).toBeDefined());
    const svg = screen.getByTestId("stats-sparkline").querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg!.querySelector("polyline")).not.toBeNull();
    expect(svg!.querySelectorAll("rect").length).toBe(2);
  });

  it("shows the totals Est. cost card", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals")).toBeDefined());
    expect(screen.getByTestId("stats-totals").textContent).toContain("$2.0000");
  });

  it("shows Free when the estimate is explicit-zero", async () => {
    const { unmount } = renderPage();
    const free = summary();
    free.totals = { ...free.totals, estimated_cost: 0, cost_source: "rates", unpriced: false };
    summaryMock.mockResolvedValue(free);
    unmount();
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals").textContent).toContain("Free"));
  });

  it("shows unpriced, never $0, when no rates exist", async () => {
    const { unmount } = renderPage();
    const unp = summary();
    unp.totals = { ...unp.totals, estimated_cost: 0, cost_source: "none", unpriced: true };
    summaryMock.mockResolvedValue(unp);
    unmount();
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals").textContent).toContain("unpriced"));
    expect(screen.getByTestId("stats-totals").textContent).not.toContain("$0.0000");
  });
});
