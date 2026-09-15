/**
 * Stats page (usage ledger surface).
 *
 * Read-only telemetry over delegation records + trace token anchors:
 * totals, per-day table, per-model / per-project / per-agent splits,
 * outcomes + error classes. Counts and shapes only — the ledger
 * never collects prompt/response text (`docs/USAGE_LEDGER_PLAN.md`).
 * Computed on read; invalidated when delegations settle.
 */
import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { api } from "@/api/client";
import { useWS } from "@/context/WSProvider";
import type { StatsCell, StatsSummary } from "@/types";

function fmt(n: number): string {
  return Math.round(n).toLocaleString();
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

function SplitTable<T extends StatsCell>({
  testId,
  title,
  rows,
  nameOf,
}: {
  testId: string;
  title: string;
  rows: T[];
  nameOf: (row: T) => { key: string; label: string };
}) {
  if (rows.length === 0) return null;
  return (
    <section data-testid={testId}>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      <div className="overflow-hidden rounded-xl border border-border/60">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/60 bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <th className="px-3 py-1.5 font-medium">Name</th>
              <th className="px-3 py-1.5 text-right font-medium">Turns</th>
              <th className="px-3 py-1.5 text-right font-medium">In</th>
              <th className="px-3 py-1.5 text-right font-medium">Peak</th>
              <th className="px-3 py-1.5 text-right font-medium">Out</th>
              <th className="px-3 py-1.5 text-right font-medium">Failed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const { key, label } = nameOf(row);
              return (
                <tr key={key} className="border-b border-border/40 last:border-0">
                  <td className="max-w-[24rem] truncate px-3 py-1.5 font-mono text-xs" title={label}>
                    {label}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.turns)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.input)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.context_input ?? 0)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.output)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.failed)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
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

  if (isLoading) {
    return (
      <div className="p-6" data-testid="stats-page">
        <p className="text-sm text-muted-foreground">Loading usage…</p>
      </div>
    );
  }

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
  const failedRate =
    t.turns > 0 ? `${((100 * t.failed) / t.turns).toFixed(1)}% failed` : undefined;

  return (
    <div className="space-y-5 p-6" data-testid="stats-page">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <Activity size={18} className="text-primary" />
          Usage
        </h1>
        <p className="text-sm text-muted-foreground">
          Last {data.window_days} days · computed on read from delegation records +
          trace token anchors · counts only, never message text
        </p>
      </header>

      {t.turns === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          No turns recorded in this window yet — run a chat turn or a delegation.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="stats-totals">
            <StatCard label="Turns" value={fmt(t.turns)} sub={failedRate} />
            <StatCard label="Input tokens" value={fmt(t.input)} sub="billed across steps" />
            <StatCard label="Peak context" value={fmt(t.context_input ?? 0)} sub="largest live context" />
            <StatCard label="Output tokens" value={fmt(t.output)} />
            <StatCard label="Cache read" value={fmt(t.cache_read)} />
            <StatCard
              label="Cost"
              value={t.cost > 0 ? t.cost.toFixed(4) : "—"}
              sub={t.cost > 0 ? undefined : "provider reports no prices"}
            />
          </div>

          <SplitTable
            testId="stats-by-day"
            title="Per day"
            rows={data.by_day}
            nameOf={(r) => ({ key: r.day, label: r.day })}
          />
          <SplitTable
            testId="stats-by-model"
            title="Per model"
            rows={data.by_model}
            nameOf={(r) => ({ key: r.model, label: r.model })}
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

          {data.by_error.length > 0 && (
            <section data-testid="stats-errors">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Failure classes
              </h2>
              <ul className="space-y-1">
                {data.by_error.map((e) => (
                  <li
                    key={e.error}
                    className="flex items-center justify-between rounded-lg border border-border/50 bg-muted/30 px-3 py-1.5 text-xs"
                  >
                    <code className="font-mono">{e.error}</code>
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
