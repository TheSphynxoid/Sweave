import { useEffect, useState } from "react";

/**
 * App-level font scaling (accessibility).
 *
 * We multiply the browser default font-size (`100%`) by a unitless scale so
 * the control is relative, not absolute — it respects a user's own browser
 * default while still letting them bump the whole UI up/down. The scale is
 * applied as a CSS custom property (`--sweave-root-scale`) on <html>, which
 * `globals.css` turns into `font-size: calc(100% * var(--sweave-root-scale))`.
 * Because Tailwind v4 sizes are `rem`-based, every `rem` utility (chrome,
 * chat thread, markdown) scales with it.
 *
 * The persisted id is read by the no-FOUC inline script in `index.html` so
 * the first paint already uses the chosen scale.
 */

export const FONT_SCALE_STORAGE_KEY = "sweave.font.scale";

export type FontScaleId = "S" | "M" | "L" | "XL";

export interface FontScaleOption {
  id: FontScaleId;
  label: string;
  /** Multiplier on the browser default (1 = 100%). */
  multiplier: number;
  /** Approximate base font size in px (assuming a 16px browser default). */
  basePx: number;
}

export const FONT_SCALE_OPTIONS: FontScaleOption[] = [
  { id: "S", label: "S", multiplier: 0.75, basePx: 12 },
  { id: "M", label: "M", multiplier: 1, basePx: 16 },
  { id: "L", label: "L", multiplier: 1.125, basePx: 18 },
  { id: "XL", label: "XL", multiplier: 1.25, basePx: 20 },
];

export const DEFAULT_FONT_SCALE_ID: FontScaleId = "M";

const VALID_IDS = new Set<string>(FONT_SCALE_OPTIONS.map((o) => o.id));

export function isFontScaleId(value: unknown): value is FontScaleId {
  return typeof value === "string" && VALID_IDS.has(value);
}

export function loadFontScaleId(): FontScaleId {
  try {
    const raw = localStorage.getItem(FONT_SCALE_STORAGE_KEY);
    if (isFontScaleId(raw)) return raw;
  } catch {
    /* localStorage unavailable */
  }
  return DEFAULT_FONT_SCALE_ID;
}

export function saveFontScaleId(id: FontScaleId): void {
  try {
    localStorage.setItem(FONT_SCALE_STORAGE_KEY, id);
  } catch {
    /* localStorage unavailable */
  }
}

export function getMultiplier(id: FontScaleId): number {
  return FONT_SCALE_OPTIONS.find((o) => o.id === id)?.multiplier ?? 1;
}

export function applyFontScale(id: FontScaleId): void {
  try {
    document.documentElement.style.setProperty(
      "--sweave-root-scale",
      String(getMultiplier(id)),
    );
  } catch {
    /* DOM unavailable */
  }
}

/** React hook: read/set the app font scale; persists and applies live. */
export function useFontScale(): [FontScaleId, (id: FontScaleId) => void] {
  const [id, setId] = useState<FontScaleId>(loadFontScaleId);

  useEffect(() => {
    applyFontScale(id);
  }, [id]);

  const set = (next: FontScaleId) => {
    saveFontScaleId(next);
    applyFontScale(next);
    setId(next);
  };

  return [id, set];
}
