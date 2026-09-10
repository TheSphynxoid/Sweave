/**
 * quietSeconds: pure unit tests.
 *
 * The TurnStatusBar shows "quiet Ns" once a running turn goes 10s
 * without output growth -- the visible signature of a wedged turn.
 */
import { describe, it, expect } from "vitest";
import { quietSeconds } from "@/components/thread/Thread";

describe("quietSeconds", () => {
  it("is zero when output just arrived", () => {
    expect(quietSeconds(42, 42)).toBe(0);
  });

  it("measures silence since output last grew", () => {
    expect(quietSeconds(100, 30)).toBe(70);
  });

  it("never goes negative (clock skew guard)", () => {
    expect(quietSeconds(5, 50)).toBe(0);
  });
});
