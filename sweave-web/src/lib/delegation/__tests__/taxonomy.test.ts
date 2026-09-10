/**
 * Honest-failure taxonomy helper tests (timeout ruling).
 *
 * A `turn_timeout_exceeded_*` error is NOT a failure: status stays
 * failed in the data model, but the UI classifies it as "timed out"
 * and extracts the budget seconds when present.
 */
import { describe, it, expect } from "vitest";
import { parseTurnTimeout, formatRuntime } from "@/lib/delegation/taxonomy";

describe("parseTurnTimeout", () => {
  it("extracts seconds from turn_timeout_exceeded_900s", () => {
    expect(parseTurnTimeout("turn_timeout_exceeded_900s")).toEqual({ seconds: 900 });
    expect(parseTurnTimeout("turn_timeout_exceeded_300s")).toEqual({ seconds: 300 });
  });

  it("matches the no-suffix future format and bare sentinel", () => {
    expect(parseTurnTimeout("turn_timeout_exceeded_600")).toEqual({ seconds: 600 });
    expect(parseTurnTimeout("turn_timeout_exceeded_")).toEqual({ seconds: null });
  });

  it("returns null for non-timeout errors", () => {
    expect(parseTurnTimeout("harness exited code 1")).toBe(null);
    expect(parseTurnTimeout(null)).toBe(null);
    expect(parseTurnTimeout("timeout while waiting for the tool result")).toBe(null);
  });

  it("matches inside longer messages", () => {
    expect(parseTurnTimeout("turn killed: turn_timeout_exceeded_1200s")).toEqual({
      seconds: 1200,
    });
  });
});

describe("formatRuntime", () => {
  it("returns null when either timestamp is missing", () => {
    expect(formatRuntime(null, "2026-09-09T10:05:05Z")).toBe(null);
    expect(formatRuntime("2026-09-09T10:00:00Z", null)).toBe(null);
  });

  it("formats short runtimes in seconds and minutes", () => {
    expect(formatRuntime("2026-09-09T10:00:00Z", "2026-09-09T10:00:45Z")).toBe("45 s");
    expect(
      formatRuntime("2026-09-09T10:00:05Z", "2026-09-09T10:15:05Z"),
    ).toBe("15 min");
  });

  it("formats long runtimes as hours + minutes", () => {
    expect(
      formatRuntime("2026-09-09T10:00:00Z", "2026-09-09T11:02:30Z"),
    ).toBe("1 h 2 min");
  });
});
