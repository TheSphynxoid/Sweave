/**
 * StatusPill (M1.9 detail surface / chat transparency).
 *
 * The honest-failure taxonomy: primarily a mirror of LiveTree's
 * pill convention (`pages/children/LiveTree.tsx` is canonical for
 * base statuses), extended per the timeout ruling: a failed
 * delegation whose error matches `turn_timeout_exceeded_*` renders
 * a calm amber "Timed out" pill — never the red "Failed" one.
 * Completed delegations also surface their elapsed runtime when
 * both timestamps are known.
 */
import { cn } from "@/utils/cn";
import type { DelegationStatus } from "@/types";
import { parseTurnTimeout, timeoutLabel, formatRuntime } from "@/lib/delegation/taxonomy";

export const STATUS_CLASS: Record<DelegationStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  review: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
};

export const STATUS_LABEL: Record<DelegationStatus, string> = {
  queued: "Queued",
  running: "Running",
  review: "Review",
  done: "Done",
  failed: "Failed",
};

export interface StatusPillProps {
  status: DelegationStatus;
  error: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  /** Overrides the default testid (status-pill-<status|timed-out>). */
  pillTestId?: string;
}

export function StatusPill({
  status,
  error,
  startedAt,
  completedAt,
  pillTestId,
}: StatusPillProps) {
  const timeout = status === "failed" ? parseTurnTimeout(error) : null;
  const effectiveTestId = pillTestId
    ? pillTestId
    : timeout
      ? "status-pill-timed-out"
      : `status-pill-${status}`;
  const runtime = timeout ? null : formatRuntime(startedAt ?? null, completedAt ?? null);
  return (
    <span className="inline-flex items-center gap-1">
      <span
        data-testid={effectiveTestId}
        className={cn(
          "rounded px-1.5 py-px text-[10px] font-medium",
          timeout
            ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
            : STATUS_CLASS[status],
        )}
      >
        {timeout ? timeoutLabel(timeout) : STATUS_LABEL[status]}
      </span>
      {runtime && (
        <span
          data-testid="status-runtime"
          className="text-[10px] text-muted-foreground"
        >
          {runtime}
        </span>
      )}
    </span>
  );
}
