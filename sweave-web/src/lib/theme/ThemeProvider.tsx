"use client";

/**
 * Centralized theme provider.
 *
 * Owns the single source of truth for the active theme (preset + custom
 * overrides), persists it, and applies it to the document. Previously each
 * consumer (Topbar switcher, Settings) loaded/applied the theme independently;
 * this context keeps them in sync and adds the "system" follow-OS behavior
 * (re-applies when the OS scheme changes while "system" is selected).
 *
 * The no-FOUC inline script in `index.html` mirrors the apply step for the
 * very first paint; this provider is the runtime source of truth afterwards.
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  type ActiveTheme,
  type ChatBackdrop,
  loadActiveTheme,
  saveActiveTheme,
  applyThemeToDocument,
  resolveSystemPresetName,
  loadBackdrop,
  saveBackdrop,
  applyBackdropToDocument,
} from "./switcher";
import { type PresetName, SYSTEM_PRESET_NAME } from "./tokens";
import type { CustomOverride } from "./tokens";

interface ThemeContextValue {
  theme: ActiveTheme;
  setPreset: (preset: PresetName) => void;
  setCustom: (custom: CustomOverride) => void;
  resetCustom: () => void;
  /** True when the user picked "system" (follow OS). */
  isSystem: boolean;
  /** Concrete preset name currently in effect (resolves "system"). */
  effectivePresetName: PresetName;
  /** Chat-thread backdrop texture (glow default; nebula/none opt-in). */
  backdrop: ChatBackdrop;
  setBackdrop: (backdrop: ChatBackdrop) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<ActiveTheme>(() => loadActiveTheme());
  const [backdrop, setBackdropState] = useState<ChatBackdrop>(() => loadBackdrop());

  // Apply on every change (and on mount).
  useEffect(() => {
    applyThemeToDocument(theme);
  }, [theme]);

  useEffect(() => {
    applyBackdropToDocument(backdrop);
  }, [backdrop]);

  // When "system" is selected, re-apply immediately if the OS flips
  // light/dark so the UI tracks the OS without a reload.
  useEffect(() => {
    if (theme.preset !== SYSTEM_PRESET_NAME) return;
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyThemeToDocument(theme);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setPreset = useCallback((preset: PresetName) => {
    setTheme((prev) => {
      const next: ActiveTheme = { ...prev, preset };
      saveActiveTheme(next);
      return next;
    });
  }, []);

  const setCustom = useCallback((custom: CustomOverride) => {
    setTheme((prev) => {
      const next: ActiveTheme = { ...prev, custom };
      saveActiveTheme(next);
      return next;
    });
  }, []);

  const resetCustom = useCallback(() => {
    setTheme((prev) => {
      const next: ActiveTheme = { ...prev, custom: {} };
      saveActiveTheme(next);
      return next;
    });
  }, []);

  const setBackdrop = useCallback((next: ChatBackdrop) => {
    saveBackdrop(next);
    setBackdropState(next);
  }, []);

  const isSystem = theme.preset === SYSTEM_PRESET_NAME;
  const effectivePresetName = isSystem ? resolveSystemPresetName() : theme.preset;

  return (
    <ThemeContext.Provider
      value={{ theme, setPreset, setCustom, resetCustom, isSystem, effectivePresetName, backdrop, setBackdrop }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a <ThemeProvider>");
  }
  return ctx;
}
