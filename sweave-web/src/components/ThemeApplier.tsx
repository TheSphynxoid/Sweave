/**
 * ThemeApplier (M1.9 Step 1).
 *
 * One-shot component that runs ``applyThemeToDocument`` on mount
 * with the persisted active theme. Renders nothing; lives inside
 * the App tree so the theme is applied on first paint and again
 * whenever the App remounts (the user reloads, etc.).
 */
import { useEffect } from "react";
import { applyThemeToDocument, loadActiveTheme } from "@/lib/theme";

export function ThemeApplier() {
  useEffect(() => {
    applyThemeToDocument(loadActiveTheme());
  }, []);
  return null;
}
