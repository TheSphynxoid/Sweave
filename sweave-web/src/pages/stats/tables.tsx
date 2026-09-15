/**
 * Stats page — reference-grade split tables.
 *
 * Each split (day / model / project / agent / kind) renders as a
 * collapsible `SplitTable`: default-open for `day` + `model`
 * (the headline breakdowns), collapsed for the rest (reference —
 * not narrative). Keeps the Est. cost + Peak columns and per-name
 * `title` tooltips; rows are anchored so future ledger dimensions
 * slot in without redesign.
 */
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn, fmt, fmtCost } from "./primitives";
import type { StatsCell } from "@/types";

export function SplitTable<T extends StatsCell>({
  testId,
  title,
  rows,
  nameOf,
  defaultOpen = false,
}: {
  testId: string;
  title: string;
  rows: T[];
  nameOf: (row: T) => { key: string; label: string };
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (rows.length === 0) return null;

  return (
    <section data-testid={testId} className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={`${testId}-body`}
        className="flex w-full items-center justify-between rounded-lg border border-border/60 bg-card/60 px-3 py-1.5 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h2>
        <span className="inline-flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="tabular-nums">{rows.length} rows</span>
          <ChevronDown
            size={14}
            className={cn("transition-transform", open ? "rotate-180" : "rotate-0")}
          />
        </span>
      </button>
      {open && (
        <div id={`${testId}-body`} className="overflow-hidden rounded-xl border border-border/60">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] text-sm">
              <thead>
                <tr className="border-b border-border/60 bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                  <th className="px-3 py-1.5 font-medium">Name</th>
                  <th className="px-3 py-1.5 text-right font-medium">Turns</th>
                  <th className="px-3 py-1.5 text-right font-medium">In</th>
                  <th className="px-3 py-1.5 text-right font-medium">Peak</th>
                  <th className="px-3 py-1.5 text-right font-medium">Out</th>
                  <th className="px-3 py-1.5 text-right font-medium">Est. cost</th>
                  <th className="px-3 py-1.5 text-right font-medium">Failed</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const { key, label } = nameOf(row);
                  const cost = fmtCost(row);
                  return (
                    <tr key={key} className="border-b border-border/40 last:border-0">
                      <td className="max-w-[24rem] truncate px-3 py-1.5 font-mono text-xs" title={label}>
                        {label}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.turns)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.input)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.context_input ?? 0)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.output)}</td>
                      <td
                        className="px-3 py-1.5 text-right tabular-nums"
                        title={row.unpriced ? "unpriced — no rates" : row.cost_source}
                      >
                        {cost.value}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.failed)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
