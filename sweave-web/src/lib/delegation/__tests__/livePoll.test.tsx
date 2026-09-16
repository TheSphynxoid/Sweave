/**
 * 4c-frontend live-poll logic (ruling Q3).
 *
 * Pins:
 *   - a running/queued delegation is live; a settled one is not;
 *   - `anyLive` is true iff any record in a list is live;
 *   - `isDetailLive` reads the latest status_timeline entry (so an
 *     open modal keyed off the detail payload agrees with the cards);
 *   - `useLivePollTick` fires `onTick` on a cadence while active and
 *     STOPS (clears the interval) the instant `active` flips false.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { useLivePollTick, anyLive, isDelegationLive, isDetailLive, LIVE_POLL_MS } from "@/lib/delegation/livePoll";
import type { Delegation, DelegationDetail } from "@/types";

function makeDelegation(status: Delegation["status"]): Delegation {
  return {
    schema_version: 5,
    delegation_id: `d-${status}`,
    task_id: "t",
    agent: "backend",
    model: "m",
    task: "task",
    status,
    created_at: "2026-09-09T10:00:00",
    updated_at: "2026-09-09T10:00:00",
    started_at: status === "queued" ? null : "2026-09-09T10:00:00",
    completed_at: status === "done" || status === "failed" || status === "review" ? "2026-09-09T10:05:00" : null,
    parent_session_id: null,
    project_name: null,
    output: "",
    error: null,
    worktree_path: null,
    branch: null,
    pr_url: null,
    parent_task_id: null,
    manifest: null,
    depth: 0,
    chain_root_id: null,
    coordination_tokens: 0,
    kind: "task",
    needs_attention: false,
  };
}

describe("liveness predicates", () => {
  it("isDelegationLive: running/queued are live, settled are not", () => {
    expect(isDelegationLive(makeDelegation("running"))).toBe(true);
    expect(isDelegationLive(makeDelegation("queued"))).toBe(true);
    expect(isDelegationLive(makeDelegation("done"))).toBe(false);
    expect(isDelegationLive(makeDelegation("failed"))).toBe(false);
    expect(isDelegationLive(makeDelegation("review"))).toBe(false);
    expect(isDelegationLive(undefined)).toBe(false);
  });

  it("anyLive: true iff any record in the list is live", () => {
    expect(anyLive([])).toBe(false);
    expect(anyLive(null)).toBe(false);
    expect(anyLive([makeDelegation("done"), makeDelegation("failed")])).toBe(false);
    expect(anyLive([makeDelegation("done"), makeDelegation("running")])).toBe(true);
    expect(anyLive([makeDelegation("queued")])).toBe(true);
  });

  it("isDetailLive reads the latest status_timeline entry", () => {
    const detailLive: DelegationDetail = {
      delegation_id: "d1",
      composed_prompt: null,
      tool_timeline: [],
      tokens: null,
      status_timeline: [
        { status: "queued", source: "h", ts: "t1" },
        { status: "running", source: "h", ts: "t2" },
      ],
    };
    const detailSettled: DelegationDetail = {
      ...detailLive,
      status_timeline: [
        { status: "queued", source: "h", ts: "t1" },
        { status: "done", source: "h", ts: "t2" },
      ],
    };
    expect(isDetailLive(detailLive)).toBe(true);
    expect(isDetailLive(detailSettled)).toBe(false);
    expect(isDetailLive(undefined)).toBe(false);
    expect(isDetailLive({ delegation_id: "x", composed_prompt: null, tool_timeline: [], tokens: null, status_timeline: [] })).toBe(false);
  });
});

// The hook calls onTick via the ref; assert via a spy component.
function SpyHarness({ active, spy }: { active: boolean; spy: () => void }) {
  useLivePollTick(active, spy);
  return null;
}

describe("useLivePollTick (spy)", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("calls the spy while active and not after settle", async () => {
    const spy = vi.fn();
    const { rerender } = render(<SpyHarness active spy={spy} />);
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS);
    expect(spy).toHaveBeenCalledTimes(1);
    rerender(<SpyHarness active={false} spy={spy} />);
    spy.mockClear();
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 3);
    expect(spy).not.toHaveBeenCalled();
  });

  it("resumes ticking if a settled record goes live again", async () => {
    const spy = vi.fn();
    const { rerender } = render(<SpyHarness active={false} spy={spy} />);
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 2);
    expect(spy).not.toHaveBeenCalled();
    rerender(<SpyHarness active spy={spy} />);
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
