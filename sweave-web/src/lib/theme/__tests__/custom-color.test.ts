/**
 * R4.1 Step 1: custom-color UI surface.
 *
 * The data path (resolveThemeTokens + applyThemeToDocument) was
 * shipped in M1.9 step 1; this file adds the picker + RGB-tuple
 * round-trip + reset helpers and pins the behaviour.
 *
 * The 8-token picker surface is tested through its pure helpers
 * (rgbTupleToHex / hexToRgbTuple) and the ActiveTheme state
 * transitions. A full React-render test of the editor lives in
 * ``custom-color-editor.test.tsx`` (uses @testing-library/react).
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  rgbTupleToHex,
  hexToRgbTuple,
  resolveThemeTokens,
  applyThemeToDocument,
  type ActiveTheme,
  type CustomOverride,
  saveActiveTheme,
  loadActiveTheme,
  THEME_DATA_ATTR,
} from "../index";
import { CUSTOM_PICKER_TOKENS } from "../../../components/CustomColorEditor";

describe("rgbTupleToHex", () => {
  it("converts 'r g b' strings to #rrggbb", () => {
    expect(rgbTupleToHex("0 0 0")).toBe("#000000");
    expect(rgbTupleToHex("255 255 255")).toBe("#ffffff");
    expect(rgbTupleToHex("167 139 250")).toBe("#a78bfa");
  });

  it("passes hex strings through unchanged", () => {
    expect(rgbTupleToHex("#a78bfa")).toBe("#a78bfa");
  });

  it("clamps out-of-range values and falls back to black on garbage", () => {
    expect(rgbTupleToHex("999 0 0")).toBe("#ff0000");
    expect(rgbTupleToHex("not a tuple")).toBe("#000000");
  });
});

describe("hexToRgbTuple", () => {
  it("converts #rrggbb to 'r g b'", () => {
    expect(hexToRgbTuple("#a78bfa")).toBe("167 139 250");
    expect(hexToRgbTuple("#000000")).toBe("0 0 0");
    expect(hexToRgbTuple("#ffffff")).toBe("255 255 255");
  });

  it("accepts hex without the leading #", () => {
    expect(hexToRgbTuple("aabbcc")).toBe("170 187 204");
  });

  it("returns '0 0 0' for invalid input", () => {
    expect(hexToRgbTuple("not a hex")).toBe("0 0 0");
    expect(hexToRgbTuple("#xyzxyz")).toBe("0 0 0");
  });
});

describe("CUSTOM_PICKER_TOKENS", () => {
  it("lists 8 surface tokens (the most-visible UI roles)", () => {
    expect(CUSTOM_PICKER_TOKENS.length).toBe(8);
    // Sanity: the listed names are a subset of the TokenName union.
    for (const token of CUSTOM_PICKER_TOKENS) {
      expect(typeof token).toBe("string");
      expect(token).toMatch(/^[a-z-]+$/);
    }
  });
});

describe("custom-color override round-trip", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("resolves a preset + custom override through the merge helper", () => {
    const theme: ActiveTheme = {
      preset: "dark",
      custom: { primary: "167 139 250" },
    };
    const resolved = resolveThemeTokens(theme);
    // The override wins for the customised token:
    expect(resolved.primary).toBe("167 139 250");
    // Non-customised tokens come from the dark preset:
    expect(resolved.background).toBe("15 23 42");
  });

  it("survives a save/load round-trip with a partial custom map", () => {
    const custom: CustomOverride = { primary: "1 2 3", border: "4 5 6" };
    saveActiveTheme({ preset: "nord", custom });
    const loaded = loadActiveTheme();
    expect(loaded.preset).toBe("nord");
    expect(loaded.custom).toEqual(custom);
  });

  it("emits a partial override in the CSS variable <style>", () => {
    applyThemeToDocument({
      preset: "dracula",
      custom: { primary: "12 34 56" },
    });
    const el = document.getElementById("sweave-theme-vars") as HTMLStyleElement | null;
    expect(el).not.toBeNull();
    const text = el!.textContent ?? "";
    expect(text).toContain(`[${THEME_DATA_ATTR}="dracula"]`);
    expect(text).toContain("--color-primary: 12 34 56;");
    // A non-customised token still flows through from the preset:
    expect(text).toContain("--color-background: 40 42 54;");
  });

  it("a full reset (custom: {}) yields the preset's bare values", () => {
    const theme: ActiveTheme = { preset: "dark", custom: {} };
    const resolved = resolveThemeTokens(theme);
    expect(resolved.background).toBe("15 23 42");
    expect(resolved.primary).toBe("167 139 250");
  });
});
