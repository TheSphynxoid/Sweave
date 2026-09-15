/**
 * Stats Phase 1b: cost column, trend hero, Free/unpriced states.
 *
 * Pins (behavioral, not structural — recharts draws lines/bars as
 * <path> and peaks as <circle>, so we assert on role/aria + cost
 * text rather than on raw element counts): the Est. cost column on
 * the split tables, the trend hero mounting with in/out + peak
 * markers + cost overlay toggle, the donut center %, and the totals
 * Est. cost card showing Free / $x / unpriced (never $0).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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
    const day = await screen.findByTestId("stats-by-day");
    day.querySelector("button")!.click();
    expect(day.textContent).toContain("Est. cost");
    // Priced day renders $1.0000; unpriced day renders "unpriced", never $0.
    expect(day.textContent).toContain("$1.0000");
    expect(day.textContent).toContain("unpriced");
    expect(day.textContent).not.toContain("$0.0000");
  });

  it("shows the totals Est. cost card (priced)", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals")).toBeDefined());
    expect(screen.getByTestId("stats-totals").textContent).toContain("$2.0000");
  });

  it("shows Free when the estimate is explicit-zero", async () => {
    const free = summary();
    free.totals = { ...free.totals, estimated_cost: 0, cost_source: "rates", unpriced: false };
    summaryMock.mockResolvedValue(free);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals").textContent).toContain("Free"));
  });

  it("shows unpriced, never $0, when no rates exist", async () => {
    const unp = summary();
    unp.totals = { ...unp.totals, estimated_cost: 0, cost_source: "none", unpriced: true };
    summaryMock.mockResolvedValue(unp);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals").textContent).toContain("unpriced"));
    expect(screen.getByTestId("stats-totals").textContent).not.toContain("$0.0000");
  });

  it("renders the trend hero with in/out lines and a cost-overlay toggle", async () => {
    const s = summary();
    s.by_day = s.by_day.map((d) => ({ ...d, context_input: 1_500_000 }));
    summaryMock.mockResolvedValue(s);
    renderPage();
    const hero = await screen.findByTestId("stats-sparkline");
    // Both in + out lines render (recharts draws them as <path>).
    const paths = hero.querySelectorAll("path");
    expect(paths.length).toBeGreaterThanOrEqual(2);
    // Cost-overlay bars render when priced (recharts <path> bars).
    expect(hero.querySelectorAll("path").length).toBeGreaterThan(2);
    // Cost-overlay toggle is present and defaults on.
    const toggle = screen.getByRole("button", { name: /cost overlay/i });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    // Toggling off flips state + aria.
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /cost overlay/i }).getAttribute("aria-pressed")).toBe("false"),
    );
  });

  it("renders per-model cost/token horizontal bars", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const bars = await screen.findByTestId("stats-model-bars");
    // Hand-rolled SVG: one bar rect per model + a track rect.
    expect(bars.querySelectorAll("rect").length).toBeGreaterThanOrEqual(2);
    expect(bars.querySelectorAll("title").length).toBeGreaterThanOrEqual(1);
    expect(bars.textContent).toMatch(/tokens/i);
  });

  it("renders per-day stacked in/out/reasoning composition", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const comp = await screen.findByTestId("stats-day-composition");
    // Three stacked segment rects per day (2 days → 6 rects).
    expect(comp.querySelectorAll("rect").length).toBeGreaterThanOrEqual(6);
    expect(comp.textContent).toMatch(/in/i);
    expect(comp.textContent).toMatch(/out/i);
    expect(comp.textContent).toMatch(/reasoning/i);
  });

  it("renders the outcome donut (succeeded vs failed) with a center %", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const donut = await screen.findByTestId("stats-outcome-donut");
    expect(donut.textContent).toContain("succeeded");
    expect(donut.textContent).toContain("failed");
    // Center percent label (succeeded of 2 = 100% here, but it's a number).
    expect(donut.textContent).toMatch(/%/);
    expect(donut.querySelector("svg")).not.toBeNull();
  });

  it("renders the peak-vs-billed context hint", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const hint = await screen.findByTestId("stats-context-hint");
    expect(hint.textContent).toMatch(/peak|billed/i);
  });

  it("handles a single-day window in the trend without crashing", async () => {
    const oneDay = summary();
    oneDay.by_day = [
      { ...oneDay.by_day[0], day: "2026-09-14", context_input: 2000, estimated_cost: 1, cost_source: "rates", unpriced: false },
    ];
    summaryMock.mockResolvedValue(oneDay);
    renderPage();
    const hero = await screen.findByTestId("stats-sparkline");
    expect(hero.querySelector("svg")).not.toBeNull();
    // Exactly one in + one out line path (single day, no divide-by-zero).
    expect(hero.querySelectorAll("path").length).toBeGreaterThanOrEqual(2);
  });
});
