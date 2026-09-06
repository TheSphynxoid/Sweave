/**
 * Sweave design tokens (M1.9 Step 1).
 *
 * Source of truth for all UI colors, spacing, and radius. Tailwind
 * config references these tokens; component code never hardcodes
 * values. Custom-color support (the v1 parity goal) extends the
 * active preset with a partial override -- the override never
 * removes tokens, only replaces them.
 *
 * Colors are stored as RGB tuples (e.g. ``"255 255 255"``) so
 * the runtime CSS can wrap them in ``rgb(var(--color-...))``.
 * The static CSS file (``src/styles/globals.css``) consumes the
 * same format. The preset names match the Tailwind theme keys
 * (1:1) so the Tailwind config doesn't need a translation
 * table.
 *
 * The exported ``RESOLVE_TOKENS`` merges a preset + custom
 * override; the merged map is what
 * ``applyThemeToDocument`` writes to the CSS variables on
 * ``:root[data-theme=...]``.
 */

export type TokenName =
  | "background"
  | "foreground"
  | "muted"
  | "muted-foreground"
  | "border"
  | "primary"
  | "primary-foreground"
  | "secondary"
  | "secondary-foreground"
  | "destructive"
  | "destructive-foreground"
  | "accent"
  | "accent-foreground"
  | "card"
  | "card-foreground"
  | "popover"
  | "popover-foreground"
  | "ring"
  | "input";

/** A complete token map. Every TokenName must be present. */
export type TokenMap = Readonly<Record<TokenName, string>>;

export interface PresetTokens {
  readonly name: string;
  readonly label: string;
  readonly tokens: TokenMap;
}

/** Custom-color override: partial TokenMap, merged on top of the active preset. */
export type CustomOverride = Readonly<Partial<TokenMap>>;

/** All five v1 presets (dark/light/dracula/nord/catppuccin) + their labels. */
export const PRESETS: readonly PresetTokens[] = [
  {
    name: "light",
    label: "Light",
    tokens: {
      background: "255 255 255",
      foreground: "15 23 42",
      muted: "241 245 249",
      "muted-foreground": "100 116 139",
      border: "226 232 240",
      primary: "139 92 246",
      "primary-foreground": "255 255 255",
      secondary: "241 245 249",
      "secondary-foreground": "15 23 42",
      destructive: "239 68 68",
      "destructive-foreground": "255 255 255",
      accent: "241 245 249",
      "accent-foreground": "15 23 42",
      card: "255 255 255",
      "card-foreground": "15 23 42",
      popover: "255 255 255",
      "popover-foreground": "15 23 42",
      ring: "139 92 246",
      input: "226 232 240",
    },
  },
  {
    name: "dark",
    label: "Dark",
    tokens: {
      background: "15 23 42",
      foreground: "248 250 252",
      muted: "30 41 59",
      "muted-foreground": "148 163 184",
      border: "30 41 59",
      primary: "167 139 250",
      "primary-foreground": "15 23 42",
      secondary: "30 41 59",
      "secondary-foreground": "248 250 252",
      destructive: "239 68 68",
      "destructive-foreground": "255 255 255",
      accent: "30 41 59",
      "accent-foreground": "248 250 252",
      card: "30 41 59",
      "card-foreground": "248 250 252",
      popover: "30 41 59",
      "popover-foreground": "248 250 252",
      ring: "167 139 250",
      input: "30 41 59",
    },
  },
  {
    name: "dracula",
    label: "Dracula",
    tokens: {
      background: "40 42 54",
      foreground: "248 248 242",
      muted: "68 71 90",
      "muted-foreground": "98 114 164",
      border: "68 71 90",
      primary: "189 147 249",
      "primary-foreground": "40 42 54",
      secondary: "68 71 90",
      "secondary-foreground": "248 248 242",
      destructive: "255 85 85",
      "destructive-foreground": "248 248 242",
      accent: "68 71 90",
      "accent-foreground": "248 248 242",
      card: "40 42 54",
      "card-foreground": "248 248 242",
      popover: "40 42 54",
      "popover-foreground": "248 248 242",
      ring: "189 147 249",
      input: "68 71 90",
    },
  },
  {
    name: "nord",
    label: "Nord",
    tokens: {
      background: "46 52 64",
      foreground: "236 239 244",
      muted: "59 66 82",
      "muted-foreground": "123 136 161",
      border: "59 66 82",
      primary: "136 192 208",
      "primary-foreground": "46 52 64",
      secondary: "59 66 82",
      "secondary-foreground": "236 239 244",
      destructive: "191 97 106",
      "destructive-foreground": "236 239 244",
      accent: "59 66 82",
      "accent-foreground": "236 239 244",
      card: "46 52 64",
      "card-foreground": "236 239 244",
      popover: "46 52 64",
      "popover-foreground": "236 239 244",
      ring: "136 192 208",
      input: "59 66 82",
    },
  },
  {
    name: "catppuccin",
    label: "Catppuccin",
    tokens: {
      background: "30 30 46",
      foreground: "205 214 244",
      muted: "49 50 68",
      "muted-foreground": "127 132 156",
      border: "69 71 90",
      primary: "203 166 247",
      "primary-foreground": "30 30 46",
      secondary: "49 50 68",
      "secondary-foreground": "205 214 244",
      destructive: "243 139 168",
      "destructive-foreground": "30 30 46",
      accent: "49 50 68",
      "accent-foreground": "205 214 244",
      card: "30 30 46",
      "card-foreground": "205 214 244",
      popover: "30 30 46",
      "popover-foreground": "205 214 244",
      ring: "203 166 247",
      input: "69 71 90",
    },
  },
] as const;

export const DEFAULT_PRESET_NAME = "dark" as const;
export type PresetName = (typeof PRESETS)[number]["name"];

export function getPreset(name: string): PresetTokens {
  const match = PRESETS.find((p) => p.name === name);
  if (!match) {
    throw new Error(`unknown preset: ${name}`);
  }
  return match;
}

export function listPresetNames(): PresetName[] {
  return PRESETS.map((p) => p.name as PresetName);
}

/**
 * Merge a preset + a partial custom override into a complete
 * token map. The override only replaces; it never removes tokens.
 * The same merge is used for the active preset + the persisted
 * custom override.
 */
export function resolveTokens(
  preset: TokenMap,
  override: CustomOverride = {},
): TokenMap {
  return { ...preset, ...override };
}

/** Convert a token map to the CSS string for a `:root[data-theme=...]` block. */
export function tokensToCssVariables(tokens: TokenMap, indent: string = "  "): string {
  return Object.entries(tokens)
    .map(([name, value]) => `${indent}--color-${name}: ${value};`)
    .join("\n");
}
