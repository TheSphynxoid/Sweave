/**
 * Custom-color editor.
 *
 * Renders every theme token as a color input, grouped by role
 * (see ``TOKEN_GROUPS``), wired to the ``ActiveTheme.custom``
 * override map. The pickers initialise to the resolved value
 * (preset + custom merge), so the user sees the active color,
 * not the raw preset value. Changes write through to the
 * parent via ``onChange(next)``; the parent persists + applies.
 *
 * A search box filters tokens by label; each group header shows
 * how many overrides it holds and offers a per-group reset.
 */
import { useCallback, useMemo, useState } from "react";
import {
  type ActiveTheme,
  type TokenName,
  ALL_TOKEN_NAMES,
  TOKEN_GROUPS,
  TOKEN_LABELS,
  hexToRgbTuple,
  resolveThemeTokens,
  rgbTupleToHex,
  saveActiveTheme,
  applyThemeToDocument,
} from "@/lib/theme";
import { Button } from "@/components/ui/button";
import { cn } from "@/utils/cn";

/**
 * Every token is editable in the picker. Kept as a named export
 * for the theme tests + any external embedders.
 */
export const CUSTOM_PICKER_TOKENS: readonly TokenName[] = ALL_TOKEN_NAMES;

interface CustomColorEditorProps {
  theme: ActiveTheme;
  /** Called when the user changes a token; the parent persists + applies. */
  onChange: (next: ActiveTheme) => void;
  /** Called when the user clicks "Reset to preset". */
  onReset: () => void;
}

export function CustomColorEditor({
  theme,
  onChange,
  onReset,
}: CustomColorEditorProps) {
  const resolved = resolveThemeTokens(theme);
  const [query, setQuery] = useState("");

  const handleColor = useCallback(
    (token: TokenName, hex: string) => {
      // Picker returns #rrggbb; persist as RGB-tuple so the
      // merged map stays consistent with the preset format
      // (and the Tailwind config + globals.css). The override
      // map is sparse: only changed tokens are stored.
      const rgb = hexToRgbTuple(hex);
      const next: ActiveTheme = {
        preset: theme.preset,
        custom: { ...theme.custom, [token]: rgb },
      };
      onChange(next);
    },
    [theme, onChange],
  );

  const handleGroupReset = useCallback(
    (tokens: readonly TokenName[]) => {
      const custom = { ...theme.custom };
      for (const token of tokens) delete custom[token];
      const next: ActiveTheme = { preset: theme.preset, custom };
      onChange(next);
    },
    [theme, onChange],
  );

  const q = query.trim().toLowerCase();
  const visibleGroups = useMemo(
    () =>
      TOKEN_GROUPS.map((group) => ({
        ...group,
        tokens: q
          ? group.tokens.filter((t) => TOKEN_LABELS[t].toLowerCase().includes(q))
          : [...group.tokens],
      })).filter((g) => g.tokens.length > 0),
    [q],
  );

  const hasOverrides = Object.keys(theme.custom).length > 0;

  return (
    <div
      data-testid="custom-color-editor"
      className="border-t border-border p-3 space-y-3"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Customize
        </span>
        <Button
          variant="ghost"
          size="sm"
          type="button"
          onClick={onReset}
          disabled={!hasOverrides}
          data-testid="custom-color-reset"
          className="text-[10px] h-auto px-2 py-1"
        >
          Reset to preset
        </Button>
      </div>
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filter colors…"
        aria-label="Filter colors"
        data-testid="custom-color-filter"
        className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
      />
      {visibleGroups.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No colors match “{query.trim()}”.
        </p>
      )}
      {visibleGroups.map((group) => {
        const overridden = group.tokens.filter((t) => theme.custom[t] !== undefined);
        return (
          <fieldset key={group.id} className="space-y-2">
            <div className="flex items-center justify-between">
              <legend className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {group.label}
                {overridden.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-primary/15 px-1.5 py-px text-[9px] text-primary">
                    {overridden.length}
                  </span>
                )}
              </legend>
              {overridden.length > 0 && (
                <button
                  type="button"
                  onClick={() => handleGroupReset(group.tokens)}
                  data-testid={`custom-color-reset-${group.id}`}
                  className="text-[10px] text-primary hover:underline"
                >
                  Reset group
                </button>
              )}
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2">
              {group.tokens.map((token) => {
                const hex = rgbTupleToHex(resolved[token]);
                const isOverridden = theme.custom[token] !== undefined;
                return (
                  <label
                    key={token}
                    className="flex items-center justify-between gap-2 text-[11px]"
                  >
                    <span
                      className={cn(
                        "truncate",
                        isOverridden ? "text-primary font-medium" : "text-foreground/80",
                      )}
                    >
                      {TOKEN_LABELS[token]}
                    </span>
                    <input
                      type="color"
                      value={hex}
                      onChange={(e) => handleColor(token, e.target.value)}
                      data-testid={`custom-color-${token}`}
                      aria-label={TOKEN_LABELS[token]}
                      className="w-6 h-6 shrink-0 rounded border border-border cursor-pointer bg-transparent"
                    />
                  </label>
                );
              })}
            </div>
          </fieldset>
        );
      })}
    </div>
  );
}

/**
 * Persist + apply a custom-color change. Convenience for the
 * parent switcher: one call handles the side effects of
 * editing a token. Pure helper; the parent still owns the
 * ``ActiveTheme`` state.
 */
export function applyCustomColorChange(next: ActiveTheme): void {
  saveActiveTheme(next);
  applyThemeToDocument(next);
}
