/**
 * Theme switcher (M1.9 Step 1, extended with 20 presets).
 *
 * The active theme is a preset name + an optional custom-color
 * override. Both are persisted in localStorage so the user's
 * choice survives page reloads. The switcher is a tiny pure
 * function (no React) so the same code is usable from a store,
 * a hook, or a one-off script.
 *
 * Storage keys:
 *   sweave.theme.preset       -- preset name (string)
 *   sweave.theme.custom       -- JSON-serialised CustomOverride
 *
 * localStorage is wrapped in try/catch because the code may run
 * in a private-browsing context where localStorage throws.
 */

import {
  CustomOverride,
  DEFAULT_PRESET_NAME,
  PresetName,
  getPreset,
  listPresetNames,
  resolveTokens,
  sanitizeCustomOverride,
  TokenMap,
  tokensToCssVariables,
} from "./tokens";

const STORAGE_KEY_PRESET = "sweave.theme.preset";
const STORAGE_KEY_CUSTOM = "sweave.theme.custom";

export interface ActiveTheme {
  preset: PresetName;
  custom: CustomOverride;
}

export const THEME_DATA_ATTR = "data-theme";

const DARK_CLASS = "dark";

/** Default theme when nothing is persisted (or localStorage is blocked). */
export function defaultActiveTheme(): ActiveTheme {
  return { preset: DEFAULT_PRESET_NAME, custom: {} };
}

function safeGet(key: string): string | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(key, value);
  } catch {
    // localStorage may be disabled (private browsing). Persistence
    // is best-effort; the active theme still works in-memory.
  }
}

/** Load the active theme from localStorage, falling back to defaults. */
export function loadActiveTheme(): ActiveTheme {
  const presetRaw = safeGet(STORAGE_KEY_PRESET);
  const customRaw = safeGet(STORAGE_KEY_CUSTOM);
  const fallback = defaultActiveTheme();
  if (!presetRaw) return fallback;
  const valid = listPresetNames();
  if (!valid.includes(presetRaw as PresetName)) return fallback;
  let custom: CustomOverride = {};
  if (customRaw) {
    try {
      custom = sanitizeCustomOverride(JSON.parse(customRaw));
    } catch {
      // Bad JSON in storage -- fall back to empty override.
      custom = {};
    }
  }
  return { preset: presetRaw as PresetName, custom };
}

/** Persist the active theme to localStorage. */
export function saveActiveTheme(theme: ActiveTheme): void {
  safeSet(STORAGE_KEY_PRESET, theme.preset);
  safeSet(STORAGE_KEY_CUSTOM, JSON.stringify(theme.custom));
}

/** Resolve a theme to the merged token map (preset + custom). */
export function resolveThemeTokens(theme: ActiveTheme): TokenMap {
  return resolveTokens(getPreset(theme.preset).tokens, theme.custom);
}

/**
 * Apply a theme to the document root by writing CSS variables
 * to `:root[data-theme=<preset>]`. The data attribute is the
 * `THEME_DATA_ATTR` constant; the variables match the Tailwind
 * config (1:1 with `TokenName`).
 *
 * Also toggles the `dark` class + `color-scheme` on the root
 * according to the preset's mode, so the Tailwind `dark:`
 * variant, the agent-elements `.dark` overrides, and native
 * form controls follow every dark preset -- not just the one
 * literally named "dark".
 *
 * Idempotent: safe to call repeatedly (e.g. on every state change).
 * Pure DOM side effect; no React.
 */
export function applyThemeToDocument(theme: ActiveTheme): void {
  if (typeof document === "undefined") return;
  const preset = getPreset(theme.preset);
  const root = document.documentElement;
  root.setAttribute(THEME_DATA_ATTR, theme.preset);
  // `dark:` variant + `.dark` CSS hooks key off this class, so
  // every dark-mode preset (dracula, nord, ...) gets dark styling.
  root.classList.toggle(DARK_CLASS, preset.mode === "dark");
  root.style.colorScheme = preset.mode;
  const merged = resolveThemeTokens(theme);
  const css = `:root[${THEME_DATA_ATTR}="${theme.preset}"] {\n${tokensToCssVariables(merged)}\n}`;
  // Inline the variables on the root so a refresh doesn't
  // require waiting for the stylesheet to load. The static
  // CSS file provides defaults for the first paint; this
  // runtime write keeps the custom override in sync.
  let styleEl = document.getElementById("sweave-theme-vars") as HTMLStyleElement | null;
  if (!styleEl) {
    styleEl = document.createElement("style");
    styleEl.id = "sweave-theme-vars";
    document.head.appendChild(styleEl);
  }
  styleEl.textContent = css;
}

/** Set the active theme (mutate in-memory + persist + apply). */
export function setActiveTheme(theme: ActiveTheme): void {
  saveActiveTheme(theme);
  applyThemeToDocument(theme);
}
