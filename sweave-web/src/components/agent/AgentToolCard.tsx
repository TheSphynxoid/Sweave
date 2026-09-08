"use client";

import { memo } from "react";
import { Check, AlertTriangle, Loader2, Wrench } from "lucide-react";
import { cn } from "@/utils/cn";

export interface AgentToolCardProps {
  tool: string;
  status: string;
  input?: unknown;
  output?: unknown;
  error?: string | null;
}

function statusMeta(status: string) {
  switch (status) {
    case "completed":
      return { label: "Completed", tone: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-500/10" };
    case "running":
    case "animating":
      return { label: "Running", tone: "text-amber-600 dark:text-amber-400", bg: "bg-amber-500/10" };
    case "error":
    case "failed":
      return { label: "Error", tone: "text-rose-600 dark:text-rose-400", bg: "bg-rose-500/10" };
    default:
      return { label: status, tone: "text-muted-foreground", bg: "bg-muted" };
  }
}

function stringify(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export const AgentToolCard = memo(function AgentToolCard({
  tool,
  status,
  input,
  output,
  error,
}: AgentToolCardProps) {
  const meta = statusMeta(status);
  const inStr = stringify(input);
  const outStr = error ? error : stringify(output);

  return (
    <div className="rounded-[10px] border border-[var(--an-tool-border-color)] bg-[var(--an-tool-background)] overflow-hidden">
      <div className="flex items-center justify-between gap-2 pl-2.5 pr-2 h-7">
        <div className="flex items-center gap-1.5 min-w-0">
          <Wrench size={13} className="shrink-0 text-[var(--an-tool-color-muted)]" />
          <span className="text-xs font-medium text-[var(--an-tool-color)] truncate">{tool}</span>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
            meta.bg,
            meta.tone,
          )}
        >
          {status === "running" || status === "animating" ? (
            <Loader2 size={10} className="animate-spin" />
          ) : status === "completed" ? (
            <Check size={10} />
          ) : status === "error" || status === "failed" ? (
            <AlertTriangle size={10} />
          ) : null}
          {meta.label}
        </span>
      </div>
      {(inStr || outStr) && (
        <div className="border-t border-[var(--an-tool-border-color)] px-2.5 py-1.5 font-mono text-[12px] leading-[16px] overflow-hidden bg-background">
          {inStr && (
            <div className="break-all text-[var(--an-tool-color)]">
              <span className="select-none text-amber-600 dark:text-amber-400">{"> "}</span>
              {inStr}
            </div>
          )}
          {outStr && (
            <div
              className={cn(
                "mt-1 whitespace-pre-wrap max-h-[120px] overflow-auto",
                error ? "text-rose-600 dark:text-rose-400" : "text-muted-foreground",
              )}
            >
              {outStr}
            </div>
          )}
        </div>
      )}
    </div>
  );
});
