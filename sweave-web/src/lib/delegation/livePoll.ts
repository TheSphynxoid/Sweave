/**
 * Live-poll helpers for the specialist detail surfaces (4c-frontend).
 *
 * Ruling Q3: while any shown delegation is `running` or `queued`,
 * refetch the detail + invalidate `["delegations"]` on a 3–5s cadence;
 * once settled, stop polling. The three standing WS events
 * (delegation.status_changed + specialist.escalated/resolved) still
 * snap the panes immediately, so a transition to settled halts the
 * interval without waiting out the tick.
 *
 * This module is the single source of truth for the cadence + the
 * liveness predicate so the cards, the Children tree, and the
 * DetailView modal all agree. The DetailView owns its own
 * `refetchInterval` (React Query handles the tick there); the cards
 * and the Children tree use `useLivePollTick` to drive their interval.
 */
import { useEffect, useRef } from "react";
import type { Delegation, DelegationDetail } from "@/types";

/** Poll cadence while a record is live (ruling Q3: 3–5s). */
export const LIVE_POLL_MS = 4000;

/** A delegation is live while it has not settled. */
export function isDelegationLive(d: Delegation | undefined): boolean {
  return d?.status === "running" || d?.status === "queued";
}

/** Liveness from a detail payload's status timeline (latest entry). */
export function isDetailLive(detail: DelegationDetail | undefined): boolean {
  if (!detail || !detail.status_timeline || detail.status_timeline.length === 0) {
    return false;
  }
  const latest = detail.status_timeline[detail.status_timeline.length - 1];
  return latest.status === "running" || latest.status === "queued";
}

/** True when ANY record in a list is live (drives the list-level poll). */
export function anyLive(delegations: Delegation[] | null | undefined): boolean {
  return !!delegations && delegations.some(isDelegationLive);
}

/**
 * Drives a 4s interval while `active` is true; stops immediately when
 * it flips false (settled). Calls `onTick` on each fire. Pure timer
 * logic, no data fetching — the caller supplies the refetch/invalidate.
 */
export function useLivePollTick(
  active: boolean,
  onTick: () => void,
  intervalMs: number = LIVE_POLL_MS,
): void {
  const onTickRef = useRef(onTick);
  onTickRef.current = onTick;

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      onTickRef.current();
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [active, intervalMs]);
}
