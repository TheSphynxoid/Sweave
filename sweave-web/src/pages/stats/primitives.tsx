/**
 * Stats page — shared primitives.
 *
 * Single source of truth for the formatting/cost rules + the card
 * chrome so every graph and table on the page speaks one visual
 * language (radius, theme tokens, tabular-nums, muted h2 hierarchy).
 *
 * Policy (DESIGN.md §8): the trend / bars / composition / donut shapes
 * are rendered with `recharts` (MIT, adopted for this page only —
 * route-level split, never global). `ContextHint` + the split tables
 * are plain HTML/CSS by design (they are reference text, not charts).
 *
 * All cost display must be Free-never-$0 and honest about unpriced data
 * (a pre-1b payload with no cost fields degrades to "unpriced",
 * never throws).
 */
import { useEffect, useRef, useState } from "react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { StatsCell } from "@/types";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Grouped thousands (INTL-agnostic digits; grouping varies by ICU). */
export function fmt(n: number): string {
  return Math.round(n).toLocaleString();
}

/** Compact token scale (1.2K / 3.4M) — axis-less bar captions. */
export function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`;
  return `${Math.round(n)}`;
}

export const EST_NONE = "unpriced";

/**
 * Estimated-cost display: Free / $x / unpriced — never $0.
 * Tolerant of pre-1b payloads (missing cost fields read as unpriced,
 * never throw — a shape gap degrades the card, not the page).
 */
export function fmtCost(cell: StatsCell): { value: string; sub?: string } {
  const est = typeof cell.estimated_cost === "number" ? cell.estimated_cost : 0;
  const unpriced = cell.unpriced ?? est <= 0;
  if (unpriced && est <= 0) {
    return { value: EST_NONE, sub: "no rates for these models" };
  }
  if (est <= 0) {
    return {
      value: "Free",
      sub: cell.cost_source === "provider" ? "provider-reported" : "free tier rates",
    };
  }
  const sub = cell.cost_source === "provider" ? "actual" : "est.";
  return { value: `$${est.toFixed(4)}`, sub };
}

/** Resolve a single cell's estimated cost as a number; unpriced → 0. */
export function costOf(cell: StatsCell): number {
  const est = typeof cell.estimated_cost === "number" ? cell.estimated_cost : 0;
  return est > 0 && !cell.unpriced ? est : 0;
}

/** Format a USD estimate as a short axis/label string (or "unpriced"). */
export function fmtCostShort(cell: StatsCell): string {
  return fmtCost(cell).value;
}

/** Relative "computed Xs ago" label from the summary's generated_at. */
export function relativeComputedAt(generatedAt: string, now: Date = new Date()): string {
  const then = new Date(generatedAt).getTime();
  const diffMs = Math.max(0, now.getTime() - then);
  const s = Math.floor(diffMs / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

/** True when the user asked the OS to minimize motion. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener?.("change", update);
    return () => mq.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

/**
 * Measure the rendered width of a wrapper element so recharts charts can
 * be responsive without `ResponsiveContainer` (which renders nothing
 * under jsdom / SSR — breaking tests + initial paint). Falls back to a
 * fixed width when the element isn't laid out yet (jsdom, first paint).
 */
export function useMeasuredWidth(fallback = 640): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const w = el.getBoundingClientRect().width;
      if (w > 0) setWidth(w);
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** Card chrome shared by charts + tables (title + body + caption). */
export function ChartCard({
  testId,
  title,
  caption,
  children,
  className,
  bodyClassName,
}: {
  testId?: string;
  title: string;
  caption?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section data-testid={testId} className={cn("space-y-2", className)}>
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      <div className={cn("rounded-xl border border-border/60 bg-card/80 px-3 py-2", bodyClassName)}>
        {children}
      </div>
      {caption && (
        <p className="text-[11px] tabular-nums text-muted-foreground">{caption}</p>
      )}
    </section>
  );
}

/** Quiet placeholder for an empty split / graph. */
export function EmptyState({ label }: { label: string }) {
  return (
    <p className="rounded-lg border border-dashed border-border/60 px-3 py-4 text-center text-[11px] text-muted-foreground">
      {label}
    </p>
  );
}
