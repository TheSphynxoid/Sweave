/**
 * Viewed-session backfill (2026-09-17, cross-tab follow-up).
 *
 * Each tab carries an explicit /chat/:sessionId: picks navigate
 * with the id, and a bare /chat resolves once (replace) to the
 * server-active session. Afterwards refreshes restore the tab's
 * own session from its URL and later global moves never touch
 * it — no cross-tab following, no storage needed.
 */
import { describe, it, expect } from "vitest";
import { shouldBackfillUrl } from "../Chat";

describe("shouldBackfillUrl", () => {
  it("backfills a bare URL from the server-active session", () => {
    expect(shouldBackfillUrl(undefined, "s-active")).toBe(true);
  });

  it("leaves explicit ids alone", () => {
    expect(shouldBackfillUrl("s-url", "s-active")).toBe(false);
    expect(shouldBackfillUrl("s-url", null)).toBe(false);
  });

  it("stays bare when nothing is active", () => {
    expect(shouldBackfillUrl(undefined, null)).toBe(false);
  });
});
