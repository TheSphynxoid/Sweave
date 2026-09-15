/**
 * Stats page (usage ledger surface).
 *
 * Read-only telemetry over delegation records + trace token anchors:
 * totals, per-day trend + composition, per-model splits, outcomes +
 * error classes. Counts and shapes only — the ledger never collects
 * prompt/response text (`docs/USAGE_LEDGER_PLAN.md`). Computed on
 * read; invalidated when delegations settle.
 *
 * Visual polish (STATS_POLISH_PLAN.md):
 *  - Ordered narrative: header + window chip → 6 totals → Trend hero
 *    → breakdown grid (per-model / composition) → (donut / context)
 *    → collapsible reference tables → failure classes.
 *  - Graphs via `recharts` (MIT; route-level split, never global) +
 *    one hand-SVG donut. Shared `ChartCard`/`FmtCost`/`FmtTok` chrome
 *    so every element speaks one visual language (radius, theme
 *    tokens, tabular-nums, muted h2 hierarchy).
 *  - Reduced-motion respected; truncation + title tooltips on long
 *    model names; responsive via measured-width (no ResponsiveContainer).
 *  - generated_at relative label ("computed Xs ago").
 *
 * Policy (DESIGN.md §8): recharts is the adopted chart lib for this
 * page only. All cost display is Free-never-$0 and honest about
 * unpriced data (pre-1b payloads degrade to "unpriced", never throw).
 */
import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import { fmt, fmtCost } from "./stats/primitives";
import {
  ContextHint,
  DayComposition,
  ModelBars,
  OutcomeDonut,
  TrendHero,
} from "./stats/charts";
import { SplitTable } from "./stats/tables";
import type { StatsSummary } from "@/types";

// ---------- Loading skeleton ----------
function StatsSkeleton() {
  return (
    <div className="space-y-5 p-6" data-testid="stats-skeleton" aria-busy="true" aria-label="Loading usage">
      <div className="h-7 w-40 animate-pulse rounded-md bg-muted/60" />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-16 animate-pulse rounded-xl border border-border/60 bg-card/60" />
        ))}
      </div>
      <div className="h-56 animate-pulse rounded-xl border border-border/60 bg-card/60" />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="h-48 animate-pulse rounded-xl border border-border/60 bg-card/60" />
        <div className="h-48 animate-pulse rounded-xl border border-border/60 bg-card/60" />
      </div>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card/80 px-4 py-3 shadow-sm">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="text-[11px] tabular-nums text-muted-foreground">{sub}</p>}
    </div>
  );
}

export function StatsPage() {
  const { subscribe } = useWS();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery<StatsSummary>({
    queryKey: ["stats-summary"],
    queryFn: () => api.getStatsSummary(),
  });

  useEffect(() => {
    const off = subscribe("delegation.status_changed", () => {
      void qc.invalidateQueries({ queryKey: ["stats-summary"] });
    });
    return () => off();
  }, [subscribe, qc]);

  const failedRate = useMemo(
    () => (data && data.totals.turns > 0 ? `${((100 * data.totals.failed) / data.totals.turns).toFixed(1)}% failed` : undefined),
    [data?.totals.turns, data?.totals.failed],
  );
  const totals = useMemo(() => {
    const t = data?.totals;
    if (!t) return [];
    return [
      { label: "Turns", value: fmt(t.turns), sub: failedRate },
      { label: "Input tokens", value: fmt(t.input), sub: "billed across steps" },
      { label: "Peak context", value: fmt(t.context_input ?? 0), sub: "largest live context" },
      { label: "Output tokens", value: fmt(t.output) },
      { label: "Cache read", value: fmt(t.cache_read) },
      { label: "Est. cost", value: fmtCost(t).value, sub: fmtCost(t).sub },
    ];
  }, [
    data?.totals.turns,
    data?.totals.input,
    data?.totals.context_input,
    data?.totals.output,
    data?.totals.cache_read,
    data?.totals.estimated_cost,
    data?.totals.cost_source,
    data?.totals.unpriced,
    data?.totals.failed,
    failedRate,
  ]);

  if (isLoading) return <StatsSkeleton />;

  if (error || !data) {
    return (
      <div className="p-6" data-testid="stats-page">
        <p className="text-sm text-rose-500">
          Failed to load usage: {error ? (error as Error).message : "no data"}
        </p>
      </div>
    );
  }

  const t = data.totals;
  const succeeded = Math.max(0, t.completed_turns - t.failed);

  return (
    <div className="space-y-5 p-6" data-testid="stats-page">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Activity size={18} className="text-primary" />
            Usage
          </h1>
          <p className="text-sm text-muted-foreground">
            Counts only, never message text
          </p>
        </div>
        <span
          className="rounded-md border border-border/60 bg-muted/40 px-2.5 py-1 text-[11px] tabular-nums text-muted-foreground"
          title={new Date(data.generated_at).toLocaleString()}
        >
          Last {data.window_days} days · computed on read
        </span>
      </header>

      {t.turns === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          No turns recorded in this window yet — run a chat turn or a delegation.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="stats-totals">
            {totals.map((c) => (
              <StatCard key={c.label} {...c} />
            ))}
          </div>

          <TrendHero rows={data.by_day} />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <ModelBars rows={data.by_model} />
            <DayComposition rows={data.by_day} />
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <OutcomeDonut succeeded={succeeded} failed={t.failed} />
            <ContextHint peak={t.context_input ?? 0} billed={t.input} />
          </div>

          <SplitTable
            testId="stats-by-day"
            title="Per day"
            rows={data.by_day}
            nameOf={(r) => ({ key: r.day, label: r.day })}
            defaultOpen
          />
          <SplitTable
            testId="stats-by-model"
            title="Per model"
            rows={data.by_model}
            nameOf={(r) => ({ key: r.model, label: r.model })}
            defaultOpen
          />
          <SplitTable
            testId="stats-by-project"
            title="Per project"
            rows={data.by_project}
            nameOf={(r) => ({ key: r.project, label: r.project })}
          />
          <SplitTable
            testId="stats-by-agent"
            title="Per specialist"
            rows={data.by_agent}
            nameOf={(r) => ({ key: r.agent, label: r.agent })}
          />
          <SplitTable
            testId="stats-by-kind"
            title="Per kind"
            rows={data.by_kind}
            nameOf={(r) => ({ key: r.kind, label: r.kind })}
          />

          {data.by_error.length > 0 && (
            <section data-testid="stats-errors">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Failure classes
              </h2>
              <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-3">
                {data.by_error.map((e) => (
                  <li
                    key={e.error}
                    className="flex items-center justify-between gap-2 rounded-lg border border-border/50 bg-muted/30 px-3 py-1.5 text-xs"
                  >
                    <code className="truncate font-mono" title={e.error}>{e.error}</code>
                    <span className="tabular-nums text-muted-foreground">×{e.count}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

export default StatsPage;
