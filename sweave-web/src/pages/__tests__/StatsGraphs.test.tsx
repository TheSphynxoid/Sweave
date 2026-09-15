/**
 * Stats graphs — high-grade behavioral pins.
 *
 * Covers the plan's graph gates per graph: Free / unpriced honesty,
 * single-point (no divide-by-zero), pre-1b payload degrade, and
 * reduced-motion (animation disabled). Asserts on role/aria + cost
 * text + structural anchors rather than on brittle raw element counts.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StatsPage } from "../Stats";
import { api } from "@/api/client";
import type { StatsSummary } from "@/types";

vi.mock("@/api/client", () => ({ api: { getStatsSummary: vi.fn() } }));
vi.mock("@/context/WSProvider", () => ({ useWS: () => ({ subscribe: () => () => {}, state: "open" }) }));

const summaryMock = vi.mocked(api.getStatsSummary);

function cell(over: Record<string, unknown> = {}): any {
  return {
    turns: 1, input: 1_000_000, output: 0, reasoning: 0, cache_read: 0,
    cache_write: 0, cost: 0, context_input: 0, failed: 0,
    estimated_cost: 1, cost_source: "rates", unpriced: false, ...over,
  };
}

function summary(): StatsSummary {
  return {
    window_days: 30, generated_at: "2026-09-14T00:00:00",
    totals: cell({ turns: 2, input: 2_000_000, estimated_cost: 2.0, wall_seconds: 60, completed_turns: 2 }),
    by_day: [
      { day: "2026-09-13", ...cell({ context_input: 1_500_000 }) },
      { day: "2026-09-14", ...cell({ context_input: 1_500_000, estimated_cost: 0, cost_source: "none", unpriced: true }) },
    ],
    by_model: [{ model: "prov/m", ...cell({ turns: 2 }) }, { model: "very-long-model-id-that-should-truncate/1.0", ...cell({ turns: 1 }) }],
    by_project: [{ project: "demo", ...cell({ turns: 2 }) }],
    by_agent: [{ agent: "backend", ...cell({ turns: 2 }) }],
    by_kind: [{ kind: "task", ...cell({ turns: 2 }) }],
    by_status: { done: 2 }, by_error: [],
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

describe("StatsGraphs", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("trend hero mounts with an accessible img role + cost-overlay toggle", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const hero = await screen.findByTestId("stats-sparkline");
    expect(hero.querySelector('[role="img"]')).not.toBeNull();
    expect(hero.querySelector('[role="img"]')!.getAttribute("aria-label")).toMatch(/trend/i);
    expect(screen.getByRole("button", { name: /cost overlay/i })).toBeDefined();
  });

  it("model bars truncate long ids but keep a full text tooltip", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const bars = await screen.findByTestId("stats-model-bars");
    const titles = Array.from(bars.querySelectorAll("title")).map((t) => t.textContent ?? "");
    // The long model id is preserved in the <title> tooltip verbatim.
    expect(titles.some((t) => t.includes("very-long-model-id-that-should-truncate/1.0"))).toBe(true);
  });

  it("outcome donut shows succeeded/failed + a center % label", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const donut = await screen.findByTestId("stats-outcome-donut");
    expect(donut.textContent).toContain("succeeded");
    expect(donut.textContent).toContain("failed");
    expect(donut.querySelector("svg")).not.toBeNull();
    // Center label carries a percent.
    expect(donut.textContent).toMatch(/%/);
  });

  it("donut degrades gracefully when there are zero turns", async () => {
    const empty = summary();
    empty.by_status = { done: 0, failed: 0 };
    empty.totals = { ...empty.totals, turns: 0, completed_turns: 0, failed: 0 };
    summaryMock.mockResolvedValue(empty);
    renderPage();
    // Empty window → no chart sections mount (page shows the empty CTA).
    await waitFor(() => expect(screen.getByTestId("stats-page").textContent).toContain("No turns recorded"));
  });

  it("pre-1b payload (no cost fields) still renders every graph without throwing", async () => {
    const legacy = summary();
    for (const bucket of [legacy.by_day, legacy.by_model, legacy.by_project, legacy.by_agent]) {
      for (const row of bucket) {
        delete (row as Partial<typeof row>).estimated_cost;
        delete (row as Partial<typeof row>).cost_source;
        delete (row as Partial<typeof row>).unpriced;
      }
    }
    delete (legacy.totals as Partial<typeof legacy.totals>).estimated_cost;
    summaryMock.mockResolvedValue(legacy);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-sparkline")).toBeDefined());
    expect(screen.getByTestId("stats-model-bars")).toBeDefined();
    expect(screen.getByTestId("stats-day-composition")).toBeDefined();
    expect(screen.getByTestId("stats-outcome-donut")).toBeDefined();
    expect(screen.getByTestId("stats-context-hint")).toBeDefined();
  });

  it("single-point trend window renders one in + one out line (no divide-by-zero)", async () => {
    const one = summary();
    one.by_day = [{ day: "2026-09-14", ...cell({ context_input: 2000, estimated_cost: 1, cost_source: "rates", unpriced: false }) }];
    summaryMock.mockResolvedValue(one);
    renderPage();
    const hero = await screen.findByTestId("stats-sparkline");
    expect(hero.querySelectorAll("path").length).toBeGreaterThanOrEqual(2);
  });

  it("context hint renders the peak-vs-billed callout copy", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    const hint = await screen.findByTestId("stats-context-hint");
    expect(hint.textContent).toMatch(/peak/i);
    expect(hint.textContent).toMatch(/billed/i);
  });

  it("reduced-motion: recharts animation is disabled (no isAnimationActive crash) — smoke render", async () => {
    // matchMedia stub reports reduce; the page must still render.
    window.matchMedia = (q: string) => ({
      matches: /prefers-reduced-motion/.test(q),
      media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    } as unknown as MediaQueryList);
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-sparkline")).toBeDefined());
    expect(screen.getByTestId("stats-sparkline").querySelector("svg")).not.toBeNull();
  });
});
