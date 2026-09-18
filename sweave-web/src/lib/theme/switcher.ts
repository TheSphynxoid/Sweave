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
  DEFAULT_LIGHT_PRESET_NAME,
  SYSTEM_PRESET_NAME,
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

/**
 * Resolve the OS color-scheme preference. Returns `true` (dark) when
 * either the OS prefers dark OR we can't tell -- matching the
 * `@theme` defaults in `globals.css`, which are dark, so the first
 * paint is never wrong for the common case.
 */
export function prefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return true;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/**
 * The real preset name the "system" selection maps to right now.
 * Dark OS -> the default dark preset; light OS -> the default light preset.
 */
export function resolveSystemPresetName(): PresetName {
  return prefersDark() ? DEFAULT_PRESET_NAME : DEFAULT_LIGHT_PRESET_NAME;
}

/** Map the active theme to a concrete preset name (resolving "system"). */
function resolveEffectivePresetName(theme: ActiveTheme): PresetName {
  return theme.preset === SYSTEM_PRESET_NAME ? resolveSystemPresetName() : theme.preset;
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
  if (!valid.includes(presetRaw as PresetName) && presetRaw !== SYSTEM_PRESET_NAME) {
    return fallback;
  }
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
  return resolveTokens(getPreset(resolveEffectivePresetName(theme)).tokens, theme.custom);
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
  const preset = getPreset(resolveEffectivePresetName(theme));
  const root = document.documentElement;
  // Keep the attribute equal to the *selected* preset (including
  // "system") so the injected selector below matches; the token
  // values are those of the *resolved* concrete preset.
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

/**
 * Chat-thread backdrop (texture axis, independent of the color theme).
 *
 * `glow` is the historical ambient (primary-tinted radial pooled at the
 * top of the thread pane) and the default, so existing users see zero
 * change. `floral` layers a subtle tileable SVG motif under the same
 * glow; `none` strips the ambient entirely. Persisted separately from
 * the preset+custom pair so adopting a backdrop never invalidates the
 * color persistence — and the no-FOUC inline script stays color-only
 * (first paint renders `glow` CSS; hydration applies the stored
 * backdrop immediately after).
 */
export type ChatBackdrop = "glow" | "floral" | "none";

const STORAGE_KEY_BACKDROP = "sweave.theme.backdrop";

export const CHAT_BACKDROP_ATTR = "data-chat-backdrop";

const BACKDROPS: readonly ChatBackdrop[] = ["glow", "floral", "none"];

export const DEFAULT_BACKDROP: ChatBackdrop = "glow";

export function listBackdrops(): ChatBackdrop[] {
  return [...BACKDROPS];
}

function isChatBackdrop(value: string): value is ChatBackdrop {
  return (BACKDROPS as readonly string[]).includes(value);
}

/** Load the chat backdrop, falling back to `glow` on anything unknown. */
export function loadBackdrop(): ChatBackdrop {
  const raw = safeGet(STORAGE_KEY_BACKDROP);
  if (raw && isChatBackdrop(raw)) return raw;
  return DEFAULT_BACKDROP;
}

/** Persist the chat backdrop (best-effort, like the theme keys). */
export function saveBackdrop(backdrop: ChatBackdrop): void {
  safeSet(STORAGE_KEY_BACKDROP, backdrop);
}

/**
 * Apply the backdrop to the document root as `data-chat-backdrop`.
 * The thread CSS (`chat-thread-ambient` in globals.css) keys off this
 * attribute. Idempotent; safe to call on every state change.
 */
export function applyBackdropToDocument(backdrop: ChatBackdrop): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute(CHAT_BACKDROP_ATTR, backdrop);
}
