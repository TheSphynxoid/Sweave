/**
 * Honest-failure taxonomy helpers for delegations.
 *
 * The user ruling (2026-09): a specialist turn killed at its turn
 * budget is NOT a failure and must never render as a bare red
 * "failed". The backend still records `status: "failed"` with an
 * error message like `turn_timeout_exceeded_900s` (soon
 * `turn_timeout_exceeded_<N>s`, and possibly a bare sentinel
 * without seconds); these helpers classify it for the UI.
 */
import type { Delegation } from "@/types";

/** Seconds limit extracted from a timeout error, or null. */
export type TurnTimeout = { seconds: number | null };

/**
 * Parse the `turn_timeout_exceeded_` sentinel + optional trailing
 * seconds out of a delegation error string. Returns the limit
 * seconds when present, `null` when the message is no timeout at
 * all, and `{ seconds: null }` for a bare timeout sentinel.
 */
export function parseTurnTimeout(error: string | null): TurnTimeout | null {
  if (!error) return null;
  const match = error.match(/turn_timeout_exceeded_(?:(\d+)s?)?/);
  if (!match) return null;
  return { seconds: match[1] ? parseInt(match[1], 10) : null };
}

export function isTimeoutDelegation(d: Pick<Delegation, "status" | "error">): boolean {
  return d.status === "failed" && parseTurnTimeout(d.error) !== null;
}

/** Human budget text for a timeout, e.g. "(after 15 min)" or "". */
export function timeoutLabel(t: TurnTimeout): string {
  if (t.seconds == null) return "⏱ Timed out";
  return `⏱ Timed out (after ${formatMinutes(t.seconds)})`;
}

function formatMinutes(seconds: number): string {
  const mins = Math.round(seconds / 60);
  return mins === 1 ? "1 min" : `${mins} min`;
}

/** Elapsed runtime between two ISO timestamps, or null when absent. */
export function formatRuntime(
  startedAt: string | null,
  completedAt: string | null,
): string | null {
  if (!startedAt || !completedAt) return null;
  const start = Date.parse(startedAt);
  const end = Date.parse(completedAt);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  const totalSec = Math.round((end - start) / 1000);
  if (totalSec < 60) return `${totalSec} s`;
  const mins = Math.floor(totalSec / 60);
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  return `${hours} h ${mins % 60} min`;
}
