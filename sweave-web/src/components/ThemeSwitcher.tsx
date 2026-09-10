/**
 * Theme switcher.
 *
 * A searchable dropdown of all presets grouped by light/dark,
 * each rendered with a multi-dot palette swatch, plus the
 * grouped "Customize" panel for per-token overrides. The
 * active preset persists to localStorage; the custom override
 * is stored separately.
 */
import { useEffect, useMemo, useState } from "react";
import { Palette, Check, Sun, Moon } from "lucide-react";
import {
  PRESETS,
  type PresetName,
  type ThemeMode,
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

function Swatch({ presetName, tokens }: { presetName: string; tokens: Record<string, string> }) {
  const dots = [
    `rgb(${tokens.background})`,
    `rgb(${tokens.primary})`,
    `rgb(${tokens.success})`,
    `rgb(${tokens.info})`,
  ];
  return (
    <span className="flex shrink-0 -space-x-1" aria-hidden>
      {dots.map((color, i) => (
        <span
          key={`${presetName}-${i}`}
          className="inline-block h-4 w-4 rounded-full ring-1 ring-border"
          style={{ background: color }}
        />
      ))}
    </span>
  );
}

function PresetGroup({
  mode,
  active,
  query,
  onChoose,
}: {
  mode: ThemeMode;
  active: PresetName;
  query: string;
  onChoose: (preset: PresetName) => void;
}) {
  const q = query.trim().toLowerCase();
  const items = PRESETS.filter(
    (p) =>
      p.mode === mode &&
      (q.length === 0 ||
        p.label.toLowerCase().includes(q) ||
        p.name.toLowerCase().includes(q)),
  );
  if (items.length === 0) return null;
  const Icon = mode === "light" ? Sun : Moon;
  return (
    <>
      <DropdownMenuLabel className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide">
        <Icon size={12} />
        <span>{mode === "light" ? "Light" : "Dark"}</span>
      </DropdownMenuLabel>
      {items.map((preset) => (
        <DropdownMenuItem
          key={preset.name}
          onSelect={() => onChoose(preset.name)}
          data-testid={`theme-preset-${preset.name}`}
          className={cn(
            "flex items-center justify-between gap-2 px-2 py-2 text-sm cursor-pointer",
            active === preset.name ? "bg-primary/10 text-primary" : "",
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <Swatch presetName={preset.name} tokens={preset.tokens as unknown as Record<string, string>} />
            <span className="min-w-0">
              <span className="block truncate">{preset.label}</span>
              <span className="block truncate text-[10px] text-muted-foreground">
                {preset.description}
              </span>
            </span>
          </span>
          {active === preset.name && <Check size={14} className="shrink-0" />}
        </DropdownMenuItem>
      ))}
    </>
  );
}

export function ThemeSwitcher() {
  const [theme, setTheme] = useState<ActiveTheme>(() => loadActiveTheme());
  const [query, setQuery] = useState("");

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

  const activePreset = useMemo(
    () => PRESETS.find((p) => p.name === theme.preset),
    [theme.preset],
  );

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
      <DropdownMenuContent align="end" className="w-80 max-h-[85vh] overflow-y-auto">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <div className="px-2 pb-1">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
            placeholder="Search themes…"
            aria-label="Search themes"
            data-testid="theme-search"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <PresetGroup mode="light" active={theme.preset} query={query} onChoose={choose} />
        <PresetGroup mode="dark" active={theme.preset} query={query} onChoose={choose} />
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
            Active: {activePreset.label} — {activePreset.description}
          </p>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
