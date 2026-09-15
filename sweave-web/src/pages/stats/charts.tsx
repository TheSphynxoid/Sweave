/**
 * Stats page — graph components (recharts, MIT).
 *
 * Five visual elements, all fed from the existing `StatsSummary`
 * payload (no API change):
 *   - TrendHero   (hero): per-day in/out area lines + peak scatter
 *                 markers + optional estimated-cost bar overlay.
 *   - ModelBars   : per-model horizontal bars (billed tokens) with
 *                 cost surfaced in the tooltip + right label.
 *   - DayComposition: per-day stacked in/out/reasoning bars.
 *   - OutcomeDonut: succeeded-vs-failed donut with center %.
 *   - ContextHint : peak-vs-billed callout (plain HTML, not a chart).
 *
 * Responsiveness is done with `useMeasuredWidth` (a wrapper + RO) rather
 * than recharts `ResponsiveContainer` — RC renders nothing under jsdom
 * and on first paint, which breaks tests and hurts LCP. Charts carry
 * explicit numeric width/height and disable animation under
 * `prefers-reduced-motion`.
 */
import { useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Info } from "lucide-react";
import {
  cn,
  costOf,
  fmt,
  fmtCost,
  fmtTok,
  useMeasuredWidth,
  useReducedMotion,
} from "./primitives";
import type { StatsCell } from "@/types";

// ---- share the page's theme tokens with recharts (CSS vars) ----
const C = {
  primary: "var(--color-primary)",
  info: "var(--color-info)",
  warning: "var(--color-warning)",
  success: "var(--color-success)",
  destructive: "var(--color-destructive)",
  muted: "var(--color-muted)",
  grid: "var(--color-border)",
  axis: "var(--color-muted-foreground)",
};

const tooltipStyle = {
  background: "var(--color-popover)",
  border: "1px solid var(--color-border)",
  borderRadius: "0.5rem",
  color: "var(--color-popover-foreground)",
  fontSize: "11px",
  padding: "6px 8px",
  boxShadow: "0 4px 16px rgb(0 0 0 / 0.25)",
};

const tooltipLabelStyle = { color: "var(--color-muted-foreground)", fontWeight: 600 };
const tooltipItemStyle = { color: "var(--color-popover-foreground)" };

// =====================================================================
// Trend hero
// =====================================================================
export function TrendHero({ rows }: { rows: Array<{ day: string } & StatsCell> }) {
  const [ref, width] = useMeasuredWidth(Math.min(720, typeof window !== "undefined" ? window.innerWidth - 64 : 720));
  const reduced = useReducedMotion();
  const [costOverlay, setCostOverlay] = useState(true);

  if (rows.length === 0) return null;

  const data = rows.map((r) => ({
    day: r.day,
    label: r.day.slice(5), // MM-DD
    input: r.input ?? 0,
    output: r.output ?? 0,
    peak: r.context_input ?? 0,
    cost: costOf(r),
    unpriced: (r.unpriced ?? costOf(r) <= 0) && costOf(r) <= 0,
  }));

  const costMax = Math.max(0, ...data.map((d) => d.cost));
  const showCost = costOverlay && costMax > 0;
  const height = 220;

  return (
    <section data-testid="stats-sparkline" aria-label="Per-day usage trend" className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Trend
        </h2>
        <button
          type="button"
          onClick={() => setCostOverlay((v) => !v)}
          aria-pressed={costOverlay}
          className="rounded-md border border-border/60 px-2 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {costOverlay ? "Cost overlay: on" : "Cost overlay: off"}
        </button>
      </div>
      <div ref={ref} className="rounded-xl border border-border/60 bg-card/80 px-2 py-3">
        <div role="img" aria-label="Per-day trend: billed input and output tokens (lines) with estimated cost overlay (bars) and peak context markers.">
          <ComposedChart width={Math.max(280, width)} height={height} data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid stroke={C.grid} strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: C.axis }}
              tickLine={false}
              axisLine={{ stroke: C.grid }}
              interval="preserveStartEnd"
              minTickGap={28}
            />
            <YAxis
              yAxisId="tok"
              tick={{ fontSize: 10, fill: C.axis }}
              tickLine={false}
              axisLine={false}
              width={40}
              tickFormatter={(v: number) => fmtTok(v)}
            />
            <YAxis yAxisId="cost" hide domain={[0, "auto"]} />
            {showCost && (
              <Bar
                yAxisId="cost"
                dataKey="cost"
                name="Est. cost"
                fill={C.primary}
                fillOpacity={0.28}
                radius={[2, 2, 0, 0]}
                isAnimationActive={!reduced}
                maxBarSize={18}
              />
            )}
            <Line
              yAxisId="tok"
              type="monotone"
              dataKey="input"
              name="In"
              stroke={C.primary}
              strokeWidth={2}
              dot={false}
              isAnimationActive={!reduced}
            />
            <Line
              yAxisId="tok"
              type="monotone"
              dataKey="output"
              name="Out"
              stroke={C.info}
              strokeWidth={2}
              dot={false}
              isAnimationActive={!reduced}
            />
            <Scatter
              yAxisId="tok"
              dataKey="peak"
              name="Peak"
              fill={C.warning}
              shape="circle"
              isAnimationActive={!reduced}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              labelStyle={tooltipLabelStyle}
              itemStyle={tooltipItemStyle}
              formatter={(value, name) => {
                const v = Number(value ?? 0);
                if (name === "Est. cost") return [`${v.toFixed(4)}`, name];
                return [fmtTok(v), name];
              }}
              labelFormatter={(l) => `Day ${l}`}
            />
            <Legend
              wrapperStyle={{ fontSize: 10 }}
              iconSize={10}
              formatter={(v) => <span className="text-muted-foreground">{v}</span>}
            />
          </ComposedChart>
        </div>
      </div>
      <p className="text-[11px] tabular-nums text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-sm" style={{ background: C.primary }} /> in</span>{" "}
        · <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-sm" style={{ background: C.info }} /> out</span>{" "}
        · <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-full" style={{ background: C.warning }} /> peak</span>
        {showCost && " · bars = est. cost"}
      </p>
    </section>
  );
}

// =====================================================================
// Per-model horizontal bars (hand-rolled SVG — recharts' vertical
// BarChart does not paint under jsdom, and the token scale needs
// custom truncation + per-row cost tooltips the lib obscures).
// =====================================================================
function modelBarHeight(rows: number): number {
  return Math.max(2, rows) * 30 + 12;
}

export function ModelBars({ rows }: { rows: Array<{ model: string } & StatsCell> }) {
  const [ref, width] = useMeasuredWidth(Math.min(420, typeof window !== "undefined" ? window.innerWidth - 64 : 420));
  if (rows.length === 0) return null;

  const labelW = 148;
  const valW = 52;
  const barW = Math.max(40, Math.min(360, width) - labelW - valW);
  const rowH = 30;
  const H = modelBarHeight(rows.length);
  const maxTok = Math.max(1, ...rows.map((r) => Math.max(r.input ?? 0, r.output ?? 0)));

  return (
    <section data-testid="stats-model-bars" aria-label="Per-model tokens and estimated cost" className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Per model
      </h2>
      <div ref={ref} className="rounded-xl border border-border/60 bg-card/80 px-2 py-2">
        <svg viewBox={`0 0 ${labelW + barW + valW} ${H}`} className="w-full" role="img" aria-label="Billed tokens per model, horizontal bars; estimated cost in the tooltip.">
          {rows.map((r, i) => {
            const y = 6 + i * rowH;
            const tok = Math.max(r.input ?? 0, r.output ?? 0);
            const w = Math.max(2, (tok / maxTok) * (barW - 6));
            const full = r.model;
            const label = full.length > 20 ? `${full.slice(0, 19)}…` : full;
            const cost = fmtCost(r);
            return (
              <g key={r.model}>
                <title>{`${full}: ${fmt(r.input)} in, ${fmt(r.output)} out, ${cost.value}${cost.sub ? ` (${cost.sub})` : ""}`}</title>
                <text x={0} y={y + rowH / 2 + 3} className="fill-muted-foreground font-mono" fontSize={10}>
                  {label}
                </text>
                <rect x={labelW} y={y + 4} width={Math.max(2, barW - 6)} height={rowH - 12} rx={3} className="fill-muted/40" />
                <rect x={labelW} y={y + 4} width={w} height={rowH - 12} rx={3} className="fill-primary/80">
                  <title>{`${full}: ${fmtTok(tok)} billed tokens`}</title>
                </rect>
                <text x={labelW + barW} y={y + rowH / 2 + 3} textAnchor="end" fontSize={9} className="fill-muted-foreground tabular-nums">
                  {fmtTok(tok)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="text-[11px] tabular-nums text-muted-foreground">
        bars = billed tokens · hover a row for est. cost
      </p>
    </section>
  );
}

// =====================================================================
// Per-day stacked composition (hand-rolled SVG — see ModelBars note).
// =====================================================================
export function DayComposition({ rows }: { rows: Array<{ day: string } & StatsCell> }) {
  const [ref, width] = useMeasuredWidth(Math.min(420, typeof window !== "undefined" ? window.innerWidth - 64 : 420));
  if (rows.length === 0) return null;

  const labelW = 44;
  const rowH = 22;
  const H = Math.max(2, rows.length) * rowH + 12;
  const barW = Math.max(40, Math.min(360, width) - labelW - 4);
  const maxStack = Math.max(
    1,
    ...rows.map((r) => (r.input ?? 0) + (r.output ?? 0) + (r.reasoning ?? 0)),
  );

  return (
    <section data-testid="stats-day-composition" aria-label="Per-day token composition" className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Daily composition
      </h2>
      <div ref={ref} className="rounded-xl border border-border/60 bg-card/80 px-2 py-2">
        <svg viewBox={`0 0 ${labelW + barW + 4} ${H}`} className="w-full" role="img" aria-label="Per-day stacked token composition: input, output, reasoning.">
          {rows.map((r, i) => {
            const y = 6 + i * rowH;
            const inW = ((r.input ?? 0) / maxStack) * barW;
            const outW = ((r.output ?? 0) / maxStack) * barW;
            const reaW = ((r.reasoning ?? 0) / maxStack) * barW;
            const x0 = labelW;
            return (
              <g key={r.day}>
                <title>{`${r.day}: in ${fmt(r.input)}, out ${fmt(r.output)}, reasoning ${fmt(r.reasoning)}`}</title>
                <text x={0} y={y + rowH / 2 + 3} fontSize={9} className="fill-muted-foreground font-mono">
                  {r.day.slice(5)}
                </text>
                <rect x={x0} y={y + 3} width={inW} height={rowH - 6} className="fill-primary" rx={1}>
                  <title>{`${r.day}: input ${fmt(r.input)}`}</title>
                </rect>
                <rect x={x0 + inW} y={y + 3} width={outW} height={rowH - 6} className="fill-info" rx={1}>
                  <title>{`${r.day}: output ${fmt(r.output)}`}</title>
                </rect>
                <rect x={x0 + inW + outW} y={y + 3} width={reaW} height={rowH - 6} className="fill-warning" rx={1}>
                  <title>{`${r.day}: reasoning ${fmt(r.reasoning)}`}</title>
                </rect>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="text-[11px] tabular-nums text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: C.primary }} /> in</span>{" "}
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: C.info }} /> out</span>{" "}
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: C.warning }} /> reasoning</span>
      </p>
    </section>
  );
}

// =====================================================================
// Outcome donut
// =====================================================================
export function OutcomeDonut({ succeeded, failed }: { succeeded: number; failed: number }) {
  const total = succeeded + failed;
  if (total === 0) return null;
  const failFrac = failed / total;
  const succFrac = 1 - failFrac;
  const pct = Math.round(succFrac * 100);

  return (
    <section data-testid="stats-outcome-donut" aria-label="Outcome split" className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Outcomes
      </h2>
      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-border/60 bg-card/80 px-3 py-2">
        <div role="img" aria-label={`${succeeded} succeeded, ${failed} failed of ${total} turns (${pct}% success)`} className="shrink-0">
          <PieChartSimple succeeded={succeeded} failed={failed} pct={pct} />
        </div>
        <ul className="space-y-1 text-xs tabular-nums">
          <li className="flex items-center justify-between gap-6">
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-full" style={{ background: C.success }} /> succeeded</span>
            <span className="text-muted-foreground">{fmt(succeeded)}</span>
          </li>
          <li className="flex items-center justify-between gap-6">
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-full" style={{ background: C.destructive }} /> failed</span>
            <span className="text-muted-foreground">{fmt(failed)}</span>
          </li>
        </ul>
      </div>
    </section>
  );
}

// Donut drawn with raw SVG (crisp center label, no animation deps) —
// the one graph where hand SVG stays cleaner than recharts.
function PieChartSimple({ succeeded, failed, pct }: { succeeded: number; failed: number; pct: number }) {
  const R = 44;
  const CIRC = 2 * Math.PI * R;
  const succLen = succFracLen(succeeded, failed, CIRC);
  const failLen = CIRC - succLen;
  const donutW = 120;
  return (
    <svg viewBox={`0 0 ${donutW} 80`} width={donutW} height={80} className="overflow-visible">
      <circle cx={60} cy={40} r={R} className="fill-none stroke-muted" strokeWidth={12} />
      <circle
        cx={60}
        cy={40}
        r={R}
        className="fill-none"
        stroke={C.success}
        strokeWidth={12}
        strokeDasharray={`${succLen} ${CIRC - succLen}`}
        strokeDashoffset={0}
        transform="rotate(-90 60 40)"
      />
      <circle
        cx={60}
        cy={40}
        r={R}
        className="fill-none"
        stroke={C.destructive}
        strokeWidth={12}
        strokeDasharray={`${failLen} ${CIRC - failLen}`}
        strokeDashoffset={-succLen}
        transform="rotate(-90 60 40)"
      />
      <text x={60} y={44} textAnchor="middle" className="fill-foreground" fontSize={15} fontWeight={700}>
        {pct}%
      </text>
      <text x={60} y={56} textAnchor="middle" className="fill-muted-foreground" fontSize={8}>
        success
      </text>
    </svg>
  );
}

function succFracLen(succeeded: number, failed: number, circ: number): number {
  const total = succeeded + failed;
  if (total <= 0) return 0;
  return (succeeded / total) * circ;
}

// =====================================================================
// Peak-vs-billed context hint (callout, not a chart)
// =====================================================================
export function ContextHint({ peak, billed }: { peak: number; billed: number }) {
  if (billed <= 0) return null;
  const ratio = peak / billed;
  return (
    <section data-testid="stats-context-hint" aria-label="Peak vs billed context" className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Context
      </h2>
      <div className={cn("flex items-start gap-2 rounded-xl border border-border/60 bg-card/80 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground")}>
        <Info size={14} className="mt-0.5 shrink-0 text-primary" />
        <p className="tabular-nums">
          Peak context <span className="text-foreground">{fmt(peak)}</span> tokens — about{" "}
          <span className="text-foreground">{ratio >= 1 ? ratio.toFixed(1) : "<1"}×</span> the{" "}
          {fmt(billed)} billed input tokens. Largest single-step prompt vs. total billed across the window.
        </p>
      </div>
    </section>
  );
}
