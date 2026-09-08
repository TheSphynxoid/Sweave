/**
 * Theme switcher (M1.9 Step 1, R4.1 Step 1, R4.4 polish).
 *
 * A dropdown of the 5 presets rendered as swatches (each shows its
 * primary color), plus a "Customize" panel for per-token overrides.
 * The active preset persists to localStorage; the custom override is
 * stored separately.
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
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

export function ThemeSwitcher() {
  const [theme, setTheme] = useState<ActiveTheme>(() => loadActiveTheme());

  useEffect(() => {
    applyThemeToDocument(theme);
  }, [theme]);

  const choose = (preset: PresetName) => {
    const next: ActiveTheme = { ...theme, preset };
    setTheme(next);
    saveActiveTheme(next);
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

  const activePreset = PRESETS.find((p) => p.name === theme.preset);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="theme-switcher-toggle"
          className="flex h-9 items-center gap-2 rounded-md border border-border bg-background px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted"
        >
          <Palette size={14} />
          <span className="hidden sm:inline capitalize">{theme.preset}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        {listPresetNames().map((name) => {
          const preset = PRESETS.find((p) => p.name === name);
          if (!preset) return null;
          const primary = `rgb(${preset.tokens.primary})`;
          return (
            <DropdownMenuItem
              key={name}
              onSelect={() => choose(name)}
              className={cn(
                "flex items-center justify-between gap-2 px-2 py-2 text-sm cursor-pointer",
                theme.preset === name ? "bg-primary/10 text-primary" : "",
              )}
            >
              <span className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="inline-block h-4 w-4 rounded-full ring-1 ring-border"
                  style={{ background: primary }}
                />
                <span>{preset.label}</span>
              </span>
              {theme.preset === name && <Check size={14} />}
            </DropdownMenuItem>
          );
        })}
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="flex items-center justify-between">
          <span>Customize</span>
          {Object.keys(theme.custom).length > 0 && (
            <button
              type="button"
              onClick={handleReset}
              className="text-[10px] text-primary hover:underline"
            >
              Reset
            </button>
          )}
        </DropdownMenuLabel>
        <CustomColorEditor theme={theme} onChange={handleCustomChange} onReset={handleReset} />
        {activePreset && (
          <p className="px-3 py-1 text-[10px] text-muted-foreground">
            Active: {activePreset.label}
          </p>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
