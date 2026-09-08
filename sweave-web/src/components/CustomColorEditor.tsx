/**
 * Custom-color editor (R4.1 Step 1).
 *
 * Renders 8 surface tokens as color inputs, wired to the
 * ``ActiveTheme.custom`` override map. The pickers initialise
 * to the resolved value (preset + custom merge), so the user
 * sees the active color, not the raw preset value. Changes
 * write through to the parent via ``onChange(partial)``; the
 * parent persists + applies.
 *
 * The 8 tokens are the most-visible surface roles; the full
 * TokenName union is 19 names and a full picker is overkill for
 * a v1 custom-color affordance. The remaining 11 are reachable
 * via the override map's escape hatch (any TokenName can be
 * passed to ``setActiveTheme({ ..., custom: { ... } })``).
 */
import { useCallback } from "react";
import {
  type ActiveTheme,
  type TokenName,
  hexToRgbTuple,
  resolveThemeTokens,
  rgbTupleToHex,
  saveActiveTheme,
  applyThemeToDocument,
} from "@/lib/theme";
import { Button } from "@/components/ui/button";

export const CUSTOM_PICKER_TOKENS: readonly TokenName[] = [
  "background",
  "foreground",
  "primary",
  "primary-foreground",
  "border",
  "muted",
  "muted-foreground",
  "accent",
] as const;

const TOKEN_LABELS: Record<TokenName, string> = {
  background: "Background",
  foreground: "Foreground",
  primary: "Primary",
  "primary-foreground": "Primary fg",
  border: "Border",
  muted: "Muted",
  "muted-foreground": "Muted fg",
  accent: "Accent",
  secondary: "Secondary",
  "secondary-foreground": "Secondary fg",
  destructive: "Destructive",
  "destructive-foreground": "Destructive fg",
  "accent-foreground": "Accent fg",
  card: "Card",
  "card-foreground": "Card fg",
  popover: "Popover",
  "popover-foreground": "Popover fg",
  ring: "Ring",
  input: "Input",
};

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

  const hasOverrides = Object.keys(theme.custom).length > 0;

  return (
    <div
      data-testid="custom-color-editor"
      className="border-t border-border p-3 space-y-2"
    >
      <div className="flex items-center justify-between">
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
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        {CUSTOM_PICKER_TOKENS.map((token) => {
          const hex = rgbTupleToHex(resolved[token]);
          return (
            <label
              key={token}
              className="flex items-center justify-between gap-2 text-[11px]"
            >
              <span className="text-foreground/80 truncate">
                {TOKEN_LABELS[token]}
              </span>
              <input
                type="color"
                value={hex}
                onChange={(e) => handleColor(token, e.target.value)}
                data-testid={`custom-color-${token}`}
                aria-label={TOKEN_LABELS[token]}
                className="w-6 h-6 rounded border border-border cursor-pointer bg-transparent"
              />
            </label>
          );
        })}
      </div>
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
