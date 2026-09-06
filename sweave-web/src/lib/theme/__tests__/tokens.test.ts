import { describe, it, expect } from "vitest";
import {
  PRESETS,
  DEFAULT_PRESET_NAME,
  listPresetNames,
  getPreset,
  resolveTokens,
  tokensToCssVariables,
  type TokenName,
} from "../tokens";

const REQUIRED_TOKENS: TokenName[] = [
  "background",
  "foreground",
  "muted",
  "muted-foreground",
  "border",
  "primary",
  "primary-foreground",
  "secondary",
  "secondary-foreground",
  "destructive",
  "destructive-foreground",
  "accent",
  "accent-foreground",
  "card",
  "card-foreground",
  "popover",
  "popover-foreground",
  "ring",
  "input",
];

const RGB_TUPLE_RE = /^\d{1,3}\s+\d{1,3}\s+\d{1,3}$/;

describe("PRESETS", () => {
  it("exposes all 5 v1 presets in stable order", () => {
    expect(PRESETS.map((p) => p.name)).toEqual([
      "light",
      "dark",
      "dracula",
      "nord",
      "catppuccin",
    ]);
  });

  it("default preset is dark (matches the pre-R4 v1 default)", () => {
    expect(DEFAULT_PRESET_NAME).toBe("dark");
  });

  it("every preset has a label and every required token", () => {
    for (const preset of PRESETS) {
      expect(preset.label).toBeTruthy();
      for (const key of REQUIRED_TOKENS) {
        expect(preset.tokens[key], `${preset.name}.${key}`).toBeTruthy();
      }
    }
  });

  it("every token is a valid RGB tuple (3 ints, whitespace-separated)", () => {
    for (const preset of PRESETS) {
      for (const key of REQUIRED_TOKENS) {
        expect(preset.tokens[key], `${preset.name}.${key}`).toMatch(RGB_TUPLE_RE);
        // Each component must be in 0-255.
        const parts = preset.tokens[key].split(/\s+/).map(Number);
        for (const p of parts) {
          expect(p).toBeGreaterThanOrEqual(0);
          expect(p).toBeLessThanOrEqual(255);
        }
      }
    }
  });
});

describe("listPresetNames", () => {
  it("returns the same names as PRESETS", () => {
    expect(listPresetNames()).toEqual(PRESETS.map((p) => p.name));
  });
});

describe("getPreset", () => {
  it("returns the matching preset for a known name", () => {
    expect(getPreset("dracula").name).toBe("dracula");
  });
  it("throws for an unknown preset name", () => {
    expect(() => getPreset("not-a-preset")).toThrow(/unknown preset/);
  });
});

describe("resolveTokens", () => {
  it("returns a complete token map when the override is empty", () => {
    const merged = resolveTokens(getPreset("dark").tokens, {});
    for (const key of REQUIRED_TOKENS) {
      expect(merged[key]).toBe(getPreset("dark").tokens[key]);
    }
  });

  it("overrides only the specified tokens (no removal)", () => {
    const merged = resolveTokens(getPreset("light").tokens, { primary: "#ff00ff" });
    expect(merged.primary).toBe("#ff00ff");
    // Everything else still comes from the preset.
    for (const key of REQUIRED_TOKENS) {
      if (key === "primary") continue;
      expect(merged[key]).toBe(getPreset("light").tokens[key]);
    }
  });
});

describe("tokensToCssVariables", () => {
  it("emits one --color-<name> line per token", () => {
    const tokens = getPreset("nord").tokens;
    const css = tokensToCssVariables(tokens);
    for (const key of REQUIRED_TOKENS) {
      expect(css).toContain(`--color-${key}: ${tokens[key]};`);
    }
  });

  it("defaults to a 2-space indent", () => {
    const css = tokensToCssVariables(getPreset("dark").tokens);
    expect(css.split("\n")[0]).toMatch(/^ {2}--color-/);
  });
});
