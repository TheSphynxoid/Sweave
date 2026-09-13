import { describe, it, expect } from "vitest";
import {
  PRESETS,
  DEFAULT_PRESET_NAME,
  ALL_TOKEN_NAMES,
  TOKEN_GROUPS,
  TOKEN_LABELS,
  listPresetNames,
  getPreset,
  getPresetMode,
  presetsByMode,
  isTokenName,
  resolveTokens,
  sanitizeCustomOverride,
  tokensToCssVariables,
  type TokenName,
} from "../tokens";

const REQUIRED_TOKENS: TokenName[] = [...ALL_TOKEN_NAMES];

const RGB_TUPLE_RE = /^\d{1,3}\s+\d{1,3}\s+\d{1,3}$/;

describe("PRESETS", () => {
  it("exposes all 29 presets in stable order", () => {
    expect(PRESETS.map((p) => p.name)).toEqual([
      "light",
      "dark",
      "dracula",
      "nord",
      "catppuccin",
      "tokyo-night",
      "onedark",
      "gruvbox-dark",
      "monokai",
      "rose-pine",
      "everforest-dark",
      "kanagawa",
      "solarized-dark",
      "github-dark",
      "midnight",
      "solarized-light",
      "gruvbox-light",
      "github-light",
      "rose-pine-dawn",
      "everforest-light",
      "carbon",
      "nebula",
      "ember",
      "night-owl",
      "ayu-mirage",
      "poimandres",
      "flexoki-dark",
      "synthwave-84",
      "vesper",
    ]);
  });

  it("default preset is carbon (canonical dark; user ruling 2026-09-13)", () => {
    expect(DEFAULT_PRESET_NAME).toBe("carbon");
  });

  it("every preset has a label, a description, a mode, and every required token", () => {
    for (const preset of PRESETS) {
      expect(preset.label).toBeTruthy();
      expect(preset.description).toBeTruthy();
      expect(["dark", "light"]).toContain(preset.mode);
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

  it("covers both modes (light presets exist for daytime use)", () => {
    expect(presetsByMode("light").length).toBeGreaterThanOrEqual(5);
    expect(presetsByMode("dark").length).toBeGreaterThanOrEqual(10);
    expect(presetsByMode("light").length + presetsByMode("dark").length).toBe(
      PRESETS.length,
    );
  });
});

describe("new dark presets — accessibility (WCAG AA)", () => {
  // Relative luminance + contrast ratio (sRGB). Used to lock in
  // that the newer dark themes keep readable text on their
  // surfaces (>= 4.5:1 for normal text; links/status use the same rule).
  // Scoped to the post-R4 additions (carbon trio + 2026-09-13 six):
  // legacy presets predate the rule and are pinned as-is.
  function luminance(rgb: string): number {
    const [r, g, b] = rgb.split(/\s+/).map(Number);
    const channel = (v: number) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  }

  function contrast(fg: string, bg: string): number {
    const l1 = luminance(fg);
    const l2 = luminance(bg);
    const [hi, lo] = l1 >= l2 ? [l1, l2] : [l2, l1];
    return (hi + 0.05) / (lo + 0.05);
  }

  // (foreground token, background token) pairs that must read clearly.
  const PAIRS: [TokenName, TokenName][] = [
    ["foreground", "background"],
    ["muted-foreground", "background"],
    ["card-foreground", "card"],
    ["popover-foreground", "popover"],
    ["sidebar-foreground", "sidebar"],
    ["topbar-foreground", "topbar"],
    ["user-message-foreground", "user-message"],
    ["assistant-message-foreground", "assistant-message"],
    ["code-foreground", "code"],
    ["primary-foreground", "primary"],
    ["destructive-foreground", "destructive"],
    ["success-foreground", "success"],
    ["warning-foreground", "warning"],
    ["info-foreground", "info"],
  ];

  for (const name of [
    "carbon",
    "nebula",
    "ember",
    "night-owl",
    "ayu-mirage",
    "poimandres",
    "flexoki-dark",
    "synthwave-84",
    "vesper",
  ] as const) {
    it(`${name} meets WCAG AA (>= 4.5:1) on every text/background pair`, () => {
      const tokens = getPreset(name).tokens;
      for (const [fg, bg] of PAIRS) {
        const ratio = contrast(tokens[fg], tokens[bg]);
        expect(ratio, `${name}: ${fg} on ${bg} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }

  // Links render on the bare background, so they get their own pin
  // (the shared PAIRS list covers component surfaces, not links).
  for (const name of [
    "night-owl",
    "ayu-mirage",
    "poimandres",
    "flexoki-dark",
    "synthwave-84",
    "vesper",
  ] as const) {
    it(`${name} link reads on the background (>= 4.5:1)`, () => {
      const tokens = getPreset(name).tokens;
      const ratio = contrast(tokens.link, tokens.background);
      expect(ratio, `${name}: link = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    });
  }
});

describe("TOKEN_GROUPS", () => {
  it("covers every token exactly once", () => {
    const grouped = TOKEN_GROUPS.flatMap((g) => [...g.tokens]);
    expect(new Set(grouped).size).toBe(grouped.length);
    expect([...grouped].sort()).toEqual([...ALL_TOKEN_NAMES].sort());
  });

  it("every token has a human label", () => {
    for (const token of ALL_TOKEN_NAMES) {
      expect(TOKEN_LABELS[token], token).toBeTruthy();
    }
  });
});

describe("isTokenName", () => {
  it("accepts known tokens and rejects unknown strings", () => {
    expect(isTokenName("primary")).toBe(true);
    expect(isTokenName("user-message")).toBe(true);
    expect(isTokenName("not-a-token")).toBe(false);
  });
});

describe("sanitizeCustomOverride", () => {
  it("keeps known tokens with string values", () => {
    expect(
      sanitizeCustomOverride({ primary: "1 2 3", link: "#aabbcc" }),
    ).toEqual({ primary: "1 2 3", link: "#aabbcc" });
  });

  it("drops unknown keys, non-strings, and blanks", () => {
    expect(
      sanitizeCustomOverride({
        primary: "1 2 3",
        bogus: "9 9 9",
        ring: 42,
        border: "   ",
      }),
    ).toEqual({ primary: "1 2 3" });
  });

  it("returns {} for non-objects", () => {
    expect(sanitizeCustomOverride(null)).toEqual({});
    expect(sanitizeCustomOverride("primary")).toEqual({});
    expect(sanitizeCustomOverride([{ primary: "1 2 3" }])).toEqual({});
  });
});

describe("getPresetMode", () => {
  it("returns the preset's mode", () => {
    expect(getPresetMode("dark")).toBe("dark");
    expect(getPresetMode("github-light")).toBe("light");
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
    expect(getPreset("tokyo-night").mode).toBe("dark");
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

  it("supports the extended tokens (sidebar, chat, status)", () => {
    const merged = resolveTokens(getPreset("midnight").tokens, {
      sidebar: "1 2 3",
      "user-message": "4 5 6",
      success: "7 8 9",
    });
    expect(merged.sidebar).toBe("1 2 3");
    expect(merged["user-message"]).toBe("4 5 6");
    expect(merged.success).toBe("7 8 9");
  });
});

describe("tokensToCssVariables", () => {
  it("emits one --color-<name> line per token", () => {
    // R4.1 step 1c: each variable carries a full rgb() value
    // (not a bare tuple) so Tailwind v4 utilities resolve
    // without arbitrary-value wrappers.
    const tokens = getPreset("nord").tokens;
    const css = tokensToCssVariables(tokens);
    for (const key of REQUIRED_TOKENS) {
      expect(css).toContain(`--color-${key}: rgb(${tokens[key]});`);
    }
  });

  it("defaults to a 2-space indent", () => {
    const css = tokensToCssVariables(getPreset("dark").tokens);
    expect(css.split("\n")[0]).toMatch(/^ {2}--color-/);
  });
});
