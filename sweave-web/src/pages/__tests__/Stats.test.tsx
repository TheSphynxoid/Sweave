/**
 * Stats page (usage ledger surface).
 *
 * Pins: page order, totals cards, logical totals order, the
 * collapsible split tables (day / model / project / agent / kind),
 * failure classes, the generated_at relative label, and the empty /
 * loading / error states — all from one `getStatsSummary` call
 * (computed on read, counts only, never message text).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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

function cell(over: Record<string, unknown> = {}): any {
  return {
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
    cost_source: "none",
    unpriced: true,
    ...over,
  };
}

function summary(): StatsSummary {
  return {
    window_days: 30,
    generated_at: "2026-09-14T00:00:00",
    totals: cell({ turns: 3, input: 1500, output: 150, failed: 1, wall_seconds: 300, completed_turns: 2 }),
    by_day: [cell({ day: "2026-09-14", turns: 3, input: 1500, output: 150, failed: 1 })],
    by_model: [cell({ model: "opencode/m", turns: 3, input: 1500 })],
    by_project: [cell({ project: "demo", turns: 3 })],
    by_agent: [cell({ agent: "backend", turns: 3 })],
    by_kind: [cell({ kind: "task", turns: 3 })],
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

  it("renders the header + window chip with a computed-ago label", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-page")).toBeDefined());
    expect(screen.getByText("Usage")).toBeDefined();
    const chip = screen.getByText(/Last 30 days/);
    expect(chip.textContent).toMatch(/computed .+ ago/);
    expect(chip.getAttribute("title")).toMatch(/\d{4}/);
  });

  it("orders totals in the logical Turns/Input/Peak/Output/Cache/Est. cost sequence", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-totals")).toBeDefined());
    const labels = Array.from(
      screen.getByTestId("stats-totals").querySelectorAll("p"),
    )
      .map((p) => p.textContent ?? "")
      .filter((t) => /Turns|Input|Peak|Output|Cache|Est\. cost/i.test(t));
    const order = labels.map((l) =>
      l.replace(/[\s\S]*?(Turns|Input|Peak|Output|Cache|Est\. cost).*/i, "$1"),
    );
    const idx = (s: string) => order.findIndex((l) => l === s);
    expect(idx("Turns")).toBeLessThan(idx("Input"));
    expect(idx("Input")).toBeLessThan(idx("Peak"));
    expect(idx("Peak")).toBeLessThan(idx("Output"));
    expect(idx("Output")).toBeLessThan(idx("Cache"));
    expect(idx("Cache")).toBeLessThan(idx("Est. cost") || idx("Cost"));
  });

  it("renders totals, day + model tables (open), and failure classes", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-by-day")).toBeDefined());
    // day + model tables default open.
    expect(screen.getByTestId("stats-by-day").textContent).toContain("2026-09-14");
    expect(screen.getByTestId("stats-by-model").textContent).toContain("opencode/m");
    // collapsed tables show their header + row count, not the body.
    const project = screen.getByTestId("stats-by-project");
    expect(project.textContent).toContain("rows");
    expect(project.querySelector("table")).toBeNull();
    // failure classes.
    expect(screen.getByTestId("stats-errors").textContent).toContain("max_steps");
    // trend + breakdown graphs mounted.
    expect(screen.getByTestId("stats-sparkline")).toBeDefined();
    expect(screen.getByTestId("stats-model-bars")).toBeDefined();
    expect(screen.getByTestId("stats-day-composition")).toBeDefined();
    expect(screen.getByTestId("stats-outcome-donut")).toBeDefined();
    expect(screen.getByTestId("stats-context-hint")).toBeDefined();
  });

  it("collapses + expands a split table", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-by-kind")).toBeDefined());
    const kind = screen.getByTestId("stats-by-kind");
    const btn = kind.querySelector("button")!;
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(kind.querySelector("table")).toBeNull();
    fireEvent.click(btn);
    expect(screen.getByTestId("stats-by-kind").querySelector("table")).not.toBeNull();
    expect(screen.getByTestId("stats-by-kind").textContent).toContain("task");
  });

  it("renders the empty state with no turns", async () => {
    summaryMock.mockResolvedValue({ ...summary(), totals: { ...summary().totals, turns: 0 } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("stats-page").textContent).toContain("No turns recorded"),
    );
  });

  it("renders a loading skeleton before data resolves", async () => {
    summaryMock.mockReturnValue(new Promise<StatsSummary>(() => {}));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-skeleton")).toBeDefined());
  });

  it("renders the by-kind split row once expanded", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(screen.getByTestId("stats-by-kind")).toBeDefined());
    fireEvent.click(screen.getByTestId("stats-by-kind").querySelector("button")!);
    expect(screen.getByTestId("stats-by-kind").textContent).toContain("task");
  });

  it("fetches the default 30-day window", async () => {
    summaryMock.mockResolvedValue(summary());
    renderPage();
    await waitFor(() => expect(summaryMock).toHaveBeenCalled());
    expect(summaryMock).toHaveBeenCalledWith();
  });

  it("degrades (never crashes) on a pre-1b payload without cost fields", async () => {
    // Stale-server shape: cells carry no estimated_cost/cost_source/
    // unpriced. The page must render with cost shown as unpriced,
    // not throw.
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
    await waitFor(() => expect(screen.getByTestId("stats-totals")).toBeDefined());
    expect(screen.getByTestId("stats-totals").textContent).toContain("unpriced");
    expect(screen.getByTestId("stats-by-day").textContent).toContain("2026-09-14");
  });
});
