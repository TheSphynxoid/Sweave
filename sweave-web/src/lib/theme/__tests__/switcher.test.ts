import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  defaultActiveTheme,
  loadActiveTheme,
  saveActiveTheme,
  applyThemeToDocument,
  resolveThemeTokens,
  THEME_DATA_ATTR,
  type ActiveTheme,
} from "../switcher";
import { listPresetNames, getPreset } from "../tokens";

describe("defaultActiveTheme", () => {
  it("returns the carbon preset with an empty custom override", () => {
    expect(defaultActiveTheme()).toEqual({ preset: "carbon", custom: {} });
  });
});

describe("saveActiveTheme + loadActiveTheme round-trip", () => {
  it("persists the preset name and the custom override", () => {
    // R4.1 step 1c: the override is stored as an RGB tuple
    // (the same shape the preset tokens use). The custom-color
    // picker writes RGB tuples via hexToRgbTuple; the runtime
    // then wraps in rgb() for the v4 @theme contract.
    const theme: ActiveTheme = { preset: "nord", custom: { primary: "171 205 239" } };
    saveActiveTheme(theme);
    const loaded = loadActiveTheme();
    expect(loaded.preset).toBe("nord");
    expect(loaded.custom).toEqual({ primary: "171 205 239" });
  });

  it("returns the default when nothing is persisted", () => {
    const loaded = loadActiveTheme();
    expect(loaded).toEqual(defaultActiveTheme());
  });

  it("returns the default when the persisted preset is unknown", () => {
    window.localStorage.setItem("sweave.theme.preset", "not-a-preset");
    window.localStorage.setItem("sweave.theme.custom", "{}");
    expect(loadActiveTheme()).toEqual(defaultActiveTheme());
  });

  it("ignores bad JSON in the custom key", () => {
    window.localStorage.setItem("sweave.theme.preset", "dark");
    window.localStorage.setItem("sweave.theme.custom", "{not json");
    const loaded = loadActiveTheme();
    expect(loaded.preset).toBe("dark");
    expect(loaded.custom).toEqual({});
  });

  it("preserves every preset across a round-trip", () => {
    for (const name of listPresetNames()) {
      const t: ActiveTheme = { preset: name, custom: {} };
      saveActiveTheme(t);
      const loaded = loadActiveTheme();
      expect(loaded.preset).toBe(name);
    }
  });
});

describe("resolveThemeTokens", () => {
  it("merges preset + custom override", () => {
    // R4.1 step 1c: the override is an RGB tuple.
    const theme: ActiveTheme = {
      preset: "light",
      custom: { primary: "18 52 86" },
    };
    const tokens = resolveThemeTokens(theme);
    expect(tokens.primary).toBe("18 52 86");
    // Other tokens come from the light preset.
    const light = getPreset("light").tokens;
    expect(tokens.background).toBe(light.background);
  });
});

describe("applyThemeToDocument", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute(THEME_DATA_ATTR);
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
    const el = document.getElementById("sweave-theme-vars");
    if (el) el.remove();
  });

  it("sets the data attribute on :root to the preset name", () => {
    applyThemeToDocument({ preset: "dracula", custom: {} });
    expect(document.documentElement.getAttribute(THEME_DATA_ATTR)).toBe("dracula");
  });

  it("writes CSS variables to a <style> element keyed by the preset", () => {
    // R4.1 step 1c: the runtime wraps custom values in rgb();
    // the override is an RGB tuple, the same shape the preset
    // tokens use.
    applyThemeToDocument({
      preset: "dark",
      custom: { primary: "170 170 170" },
    });
    const el = document.getElementById("sweave-theme-vars") as HTMLStyleElement | null;
    expect(el).not.toBeNull();
    const text = el!.textContent ?? "";
    expect(text).toContain(`[${THEME_DATA_ATTR}="dark"]`);
    expect(text).toContain("--color-primary: rgb(170 170 170);");
  });

  it("is idempotent: re-applying the same theme replaces the <style> text", () => {
    applyThemeToDocument({ preset: "nord", custom: {} });
    const el1 = document.getElementById("sweave-theme-vars") as HTMLStyleElement | null;
    const before = el1?.textContent ?? "";
    applyThemeToDocument({ preset: "nord", custom: { primary: "255 0 255" } });
    const el2 = document.getElementById("sweave-theme-vars") as HTMLStyleElement | null;
    const after = el2?.textContent ?? "";
    expect(el1).toBe(el2); // same element
    expect(after).not.toBe(before);
    expect(after).toContain("--color-primary: rgb(255 0 255);");
  });

  it("toggles the dark class + color-scheme per preset mode", () => {
    applyThemeToDocument({ preset: "dracula", custom: {} });
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
    applyThemeToDocument({ preset: "github-light", custom: {} });
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("emits the extended tokens (sidebar, status, chat, code)", () => {
    applyThemeToDocument({ preset: "midnight", custom: {} });
    const el = document.getElementById("sweave-theme-vars") as HTMLStyleElement | null;
    const text = el?.textContent ?? "";
    for (const key of ["sidebar", "success", "warning", "info", "user-message", "code", "link", "selection"] as const) {
      expect(text).toContain(`--color-${key}: rgb(`);
    }
  });
});

describe("loadActiveTheme sanitization", () => {
  it("drops unknown custom keys persisted by older builds", () => {
    window.localStorage.setItem("sweave.theme.preset", "dark");
    window.localStorage.setItem(
      "sweave.theme.custom",
      JSON.stringify({ primary: "1 2 3", "no-such-token": "9 9 9" }),
    );
    expect(loadActiveTheme()).toEqual({
      preset: "dark",
      custom: { primary: "1 2 3" },
    });
  });
});

describe("system preset", () => {
  const realMatchMedia = window.matchMedia;

  afterEach(() => {
    window.matchMedia = realMatchMedia;
  });

  it("loadActiveTheme accepts a persisted system selection", () => {
    window.localStorage.setItem("sweave.theme.preset", "system");
    expect(loadActiveTheme()).toEqual({ preset: "system", custom: {} });
  });

  it("resolveThemeTokens resolves system to carbon on a dark OS", () => {
    const tokens = resolveThemeTokens({ preset: "system", custom: {} });
    expect(tokens).toEqual(getPreset("carbon").tokens);
  });

  it("resolveThemeTokens resolves system to light on a light OS", () => {
    window.matchMedia = (() => ({ matches: false })) as unknown as typeof window.matchMedia;
    const tokens = resolveThemeTokens({ preset: "system", custom: {} });
    expect(tokens).toEqual(getPreset("light").tokens);
  });

  it("applyThemeToDocument keeps data-theme=system and follows the OS mode", () => {
    applyThemeToDocument({ preset: "system", custom: {} });
    expect(document.documentElement.getAttribute(THEME_DATA_ATTR)).toBe("system");
    // jsdom has no matchMedia: prefersDark() falls back to dark.
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("applyThemeToDocument clears the dark class for system on a light OS", () => {
    window.matchMedia = (() => ({ matches: false })) as unknown as typeof window.matchMedia;
    applyThemeToDocument({ preset: "system", custom: {} });
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe("light");
  });
});
