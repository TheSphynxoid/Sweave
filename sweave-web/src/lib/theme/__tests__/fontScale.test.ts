import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  FONT_SCALE_STORAGE_KEY,
  FONT_SCALE_OPTIONS,
  DEFAULT_FONT_SCALE_ID,
  isFontScaleId,
  loadFontScaleId,
  saveFontScaleId,
  getMultiplier,
  applyFontScale,
} from "../fontScale";

describe("fontScale", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.removeProperty("--sweave-root-scale");
  });

  it("exposes 4 ordered options S<M<L<XL with increasing multipliers", () => {
    expect(FONT_SCALE_OPTIONS.map((o) => o.id)).toEqual(["S", "M", "L", "XL"]);
    const mult = FONT_SCALE_OPTIONS.map((o) => o.multiplier);
    expect(mult).toEqual([0.75, 1, 1.125, 1.25]);
  });

  it("isFontScaleId validates against the known set", () => {
    expect(isFontScaleId("M")).toBe(true);
    expect(isFontScaleId("s")).toBe(false);
    expect(isFontScaleId("XXL")).toBe(false);
    expect(isFontScaleId(42)).toBe(false);
  });

  it("loadFontScaleId falls back to default when storage is empty/garbage", () => {
    expect(loadFontScaleId()).toBe(DEFAULT_FONT_SCALE_ID);
    localStorage.setItem(FONT_SCALE_STORAGE_KEY, "not-a-real-id");
    expect(loadFontScaleId()).toBe(DEFAULT_FONT_SCALE_ID);
  });

  it("save then load round-trips", () => {
    saveFontScaleId("XL");
    expect(localStorage.getItem(FONT_SCALE_STORAGE_KEY)).toBe("XL");
    expect(loadFontScaleId()).toBe("XL");
  });

  it("getMultiplier maps ids to their multiplier (default 1 for unknown)", () => {
    expect(getMultiplier("S")).toBe(0.75);
    expect(getMultiplier("M")).toBe(1);
    expect(getMultiplier("XL")).toBe(1.25);
  });

  it("applyFontScale writes --sweave-root-scale as the multiplier string", () => {
    applyFontScale("L");
    expect(document.documentElement.style.getPropertyValue("--sweave-root-scale")).toBe(
      "1.125",
    );
    applyFontScale("S");
    expect(document.documentElement.style.getPropertyValue("--sweave-root-scale")).toBe(
      "0.75",
    );
  });
});
