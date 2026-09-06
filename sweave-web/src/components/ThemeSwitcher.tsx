/**
 * Theme switcher (M1.9 Step 1, R4.1 Step 1).
 *
 * v1 parity: a dropdown listing the 5 presets + a "Customize"
 * panel (R4.1) for per-token color overrides. The active preset
 * is stored in localStorage; the custom override is stored as a
 * partial TokenMap under a separate key.
 */
import { useEffect, useState } from "react";
import { Palette, Check } from "lucide-react";
import {
  PRESETS,
  listPresetNames,
  type PresetName,
  loadActiveTheme,
  saveActiveTheme,
  applyThemeToDocument,
  type ActiveTheme,
} from "@/lib/theme";
import { cn } from "@/utils/cn";
import {
  CustomColorEditor,
  applyCustomColorChange,
} from "./CustomColorEditor";

export function ThemeSwitcher() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ActiveTheme>(() => loadActiveTheme());

  // Re-apply on mount in case the AppProvider remounted after
  // the initial document was loaded (the user navigating to
  // a sub-page preserves the theme).
  useEffect(() => {
    applyThemeToDocument(theme);
  }, [theme]);

  const choose = (preset: PresetName) => {
    const next: ActiveTheme = { ...theme, preset };
    setTheme(next);
    saveActiveTheme(next);
    setOpen(false);
  };

  const handleCustomChange = (next: ActiveTheme) => {
    setTheme(next);
    applyCustomColorChange(next);
  };

  const handleReset = () => {
    const next: ActiveTheme = { ...theme, custom: {} };
    setTheme(next);
    saveActiveTheme(next);
    applyThemeToDocument(next);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        data-testid="theme-switcher-toggle"
        className="flex items-center gap-2 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Palette size={14} />
        <span className="capitalize">{theme.preset}</span>
      </button>
      {open && (
        <div
          data-testid="theme-switcher-menu"
          role="menu"
          className="absolute right-0 mt-1 w-64 border border-border bg-card rounded shadow-lg z-50"
        >
          {listPresetNames().map((name) => {
            const preset = PRESETS.find((p) => p.name === name);
            if (!preset) return null;
            return (
              <button
                key={name}
                type="button"
                role="menuitemradio"
                aria-checked={theme.preset === name}
                onClick={() => choose(name)}
                data-testid={`theme-option-${name}`}
                className={cn(
                  "w-full flex items-center justify-between gap-2 px-3 py-2 text-xs text-left",
                  theme.preset === name
                    ? "bg-primary/10 text-primary"
                    : "text-foreground hover:bg-muted",
                )}
              >
                <span className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="inline-block w-3 h-3 rounded"
                    style={{ background: `rgb(var(--color-primary))` }}
                  />
                  <span>{preset.label}</span>
                </span>
                {theme.preset === name && <Check size={14} />}
              </button>
            );
          })}
          <CustomColorEditor
            theme={theme}
            onChange={handleCustomChange}
            onReset={handleReset}
          />
        </div>
      )}
    </div>
  );
}
