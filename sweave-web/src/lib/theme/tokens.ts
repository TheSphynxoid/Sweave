/**
 * Sweave design tokens.
 *
 * Source of truth for all UI colors. Tailwind config references
 * these tokens; component code never hardcodes values.
 * Custom-color support extends the active preset with a partial
 * override -- the override never removes tokens, only replaces
 * them.
 *
 * Colors are stored as RGB tuples (e.g. ``"255 255 255"``) so
 * the runtime CSS can wrap them in ``rgb(var(--color-...))``.
 * The static CSS file (``src/styles/globals.css``) consumes the
 * same format. The preset names match the Tailwind theme keys
 * (1:1) so the Tailwind config doesn't need a translation
 * table.
 *
 * Token families:
 *   core (19)     -- the original shadcn-style surface roles.
 *                     Every preset defines these explicitly.
 *   extended (18) -- status (success/warning/info), chrome
 *                     (sidebar/topbar), chat bubbles, code,
 *                     links, selection. Presets override the
 *                     ones that give them character; anything
 *                     left out falls back to a mode-aware
 *                     derivation from the core map (see
 *                     ``definePreset``), so every ``TokenMap``
 *                     is still complete.
 *
 * The exported ``RESOLVE_TOKENS`` merges a preset + custom
 * override; the merged map is what
 * ``applyThemeToDocument`` writes to the CSS variables on
 * ``:root[data-theme=...]``.
 */

export type ThemeMode = "dark" | "light";

export type CoreTokenName =
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

export type ExtendedTokenName =
  | "success"
  | "success-foreground"
  | "warning"
  | "warning-foreground"
  | "info"
  | "info-foreground"
  | "sidebar"
  | "sidebar-foreground"
  | "topbar"
  | "topbar-foreground"
  | "user-message"
  | "user-message-foreground"
  | "assistant-message"
  | "assistant-message-foreground"
  | "code"
  | "code-foreground"
  | "link"
  | "selection";

export type TokenName = CoreTokenName | ExtendedTokenName;

/** A complete token map. Every TokenName must be present. */
export type TokenMap = Readonly<Record<TokenName, string>>;

export interface PresetTokens {
  readonly name: string;
  readonly label: string;
  readonly mode: ThemeMode;
  readonly description: string;
  readonly tokens: TokenMap;
}

/** Custom-color override: partial TokenMap, merged on top of the active preset. */
export type CustomOverride = Readonly<Partial<TokenMap>>;

/** Every token name, core first then extended. */
export const ALL_TOKEN_NAMES: readonly TokenName[] = [
  "background",
  "foreground",
  "muted",
  "muted-foreground",
  "border",
  "primary",
  "primary-foreground",
  "secondary",
  "secondary-foreground",
  "destructive",
  "destructive-foreground",
  "accent",
  "accent-foreground",
  "card",
  "card-foreground",
  "popover",
  "popover-foreground",
  "ring",
  "input",
  "success",
  "success-foreground",
  "warning",
  "warning-foreground",
  "info",
  "info-foreground",
  "sidebar",
  "sidebar-foreground",
  "topbar",
  "topbar-foreground",
  "user-message",
  "user-message-foreground",
  "assistant-message",
  "assistant-message-foreground",
  "code",
  "code-foreground",
  "link",
  "selection",
] as const;

const ALL_TOKEN_SET: ReadonlySet<string> = new Set(ALL_TOKEN_NAMES);

export function isTokenName(name: string): name is TokenName {
  return ALL_TOKEN_SET.has(name);
}

/**
 * Editor grouping for the customize panel. The groups are the
 * source of truth for ``CustomColorEditor`` section order; every
 * TokenName appears in exactly one group.
 */
export interface TokenGroup {
  readonly id: string;
  readonly label: string;
  readonly tokens: readonly TokenName[];
}

export const TOKEN_GROUPS: readonly TokenGroup[] = [
  {
    id: "base",
    label: "Base",
    tokens: ["background", "foreground", "border", "ring", "input", "selection"],
  },
  {
    id: "brand",
    label: "Brand",
    tokens: [
      "primary",
      "primary-foreground",
      "secondary",
      "secondary-foreground",
      "accent",
      "accent-foreground",
      "link",
    ],
  },
  {
    id: "surfaces",
    label: "Surfaces",
    tokens: [
      "card",
      "card-foreground",
      "popover",
      "popover-foreground",
      "muted",
      "muted-foreground",
      "sidebar",
      "sidebar-foreground",
      "topbar",
      "topbar-foreground",
    ],
  },
  {
    id: "status",
    label: "Status",
    tokens: [
      "success",
      "success-foreground",
      "warning",
      "warning-foreground",
      "info",
      "info-foreground",
      "destructive",
      "destructive-foreground",
    ],
  },
  {
    id: "chat-code",
    label: "Chat & code",
    tokens: [
      "user-message",
      "user-message-foreground",
      "assistant-message",
      "assistant-message-foreground",
      "code",
      "code-foreground",
    ],
  },
] as const;

export const TOKEN_LABELS: Record<TokenName, string> = {
  background: "Background",
  foreground: "Foreground",
  muted: "Muted",
  "muted-foreground": "Muted fg",
  border: "Border",
  primary: "Primary",
  "primary-foreground": "Primary fg",
  secondary: "Secondary",
  "secondary-foreground": "Secondary fg",
  destructive: "Destructive",
  "destructive-foreground": "Destructive fg",
  accent: "Accent",
  "accent-foreground": "Accent fg",
  card: "Card",
  "card-foreground": "Card fg",
  popover: "Popover",
  "popover-foreground": "Popover fg",
  ring: "Ring",
  input: "Input",
  success: "Success",
  "success-foreground": "Success fg",
  warning: "Warning",
  "warning-foreground": "Warning fg",
  info: "Info",
  "info-foreground": "Info fg",
  sidebar: "Sidebar",
  "sidebar-foreground": "Sidebar fg",
  topbar: "Topbar",
  "topbar-foreground": "Topbar fg",
  "user-message": "User message",
  "user-message-foreground": "User msg fg",
  "assistant-message": "Assistant msg",
  "assistant-message-foreground": "Assist msg fg",
  code: "Code bg",
  "code-foreground": "Code fg",
  link: "Link",
  selection: "Selection",
};

/** Drop unknown keys / non-string values from a persisted override. */
export function sanitizeCustomOverride(raw: unknown): CustomOverride {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (isTokenName(key) && typeof value === "string" && value.trim().length > 0) {
      out[key] = value;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Preset builder
// ---------------------------------------------------------------------------

interface PresetInput<N extends string> {
  readonly name: N;
  readonly label: string;
  readonly mode: ThemeMode;
  readonly description: string;
  readonly core: Record<CoreTokenName, string>;
  readonly extend?: Partial<Record<ExtendedTokenName, string>>;
}

/**
 * Build a complete preset from its 19 core tokens + optional
 * extended overrides. Anything not overridden derives from the
 * core map (chrome bubbles reuse card/muted roles) or from a
 * mode-aware generic (status hues that read on dark vs light).
 */
function definePreset<N extends string>(
  input: PresetInput<N>,
): PresetTokens & { readonly name: N } {
  const { core, extend = {}, mode } = input;
  const dark = mode === "dark";
  const derived: Record<ExtendedTokenName, string> = {
    success: dark ? "52 211 153" : "22 163 74",
    "success-foreground": dark ? "15 23 42" : "255 255 255",
    warning: dark ? "251 191 36" : "217 119 6",
    "warning-foreground": dark ? "15 23 42" : "255 255 255",
    info: dark ? "56 189 248" : "2 132 199",
    "info-foreground": dark ? "15 23 42" : "255 255 255",
    sidebar: core.card,
    "sidebar-foreground": core["card-foreground"],
    topbar: core.card,
    "topbar-foreground": core["card-foreground"],
    "user-message": core.muted,
    "user-message-foreground": core.foreground,
    "assistant-message": core.card,
    "assistant-message-foreground": core["card-foreground"],
    code: dark ? "10 10 10" : "24 24 27",
    "code-foreground": dark ? "212 212 216" : "244 244 245",
    link: core.primary,
    selection: dark ? core.muted : "191 219 254",
  };
  const tokens = { ...core, ...derived, ...extend } as TokenMap;
  return {
    name: input.name,
    label: input.label,
    mode,
    description: input.description,
    tokens,
  };
}

// ---------------------------------------------------------------------------
// Presets (29): the 5 originals + 24 community-palette additions.
// ---------------------------------------------------------------------------

/** All twenty-nine presets (light/dark + community palettes) + their labels. */
export const PRESETS = [
  definePreset({
    name: "light",
    label: "Light",
    mode: "light",
    description: "Clean default light theme.",
    core: {
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
    extend: {
      link: "139 92 246",
    },
  }),
  definePreset({
    name: "dark",
    label: "Dark",
    mode: "dark",
    description: "Default slate dark theme.",
    core: {
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
    extend: {
      selection: "71 85 105",
    },
  }),
  definePreset({
    name: "dracula",
    label: "Dracula",
    mode: "dark",
    description: "The classic Dracula palette: purple haze over dark slate.",
    core: {
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
    extend: {
      success: "80 250 123",
      "success-foreground": "40 42 54",
      warning: "241 250 140",
      "warning-foreground": "40 42 54",
      info: "139 233 253",
      "info-foreground": "40 42 54",
      code: "33 34 44",
      "code-foreground": "248 248 242",
      link: "139 233 253",
      selection: "68 71 90",
    },
  }),
  definePreset({
    name: "nord",
    label: "Nord",
    mode: "dark",
    description: "Arctic, north-bluish calm.",
    core: {
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
    extend: {
      success: "163 190 140",
      "success-foreground": "46 52 64",
      warning: "235 203 139",
      "warning-foreground": "46 52 64",
      info: "129 161 193",
      "info-foreground": "46 52 64",
      code: "36 41 51",
      "code-foreground": "236 239 244",
      link: "136 192 208",
      selection: "76 86 106",
    },
  }),
  definePreset({
    name: "catppuccin",
    label: "Catppuccin",
    mode: "dark",
    description: "Catppuccin Mocha: cozy pastel dark.",
    core: {
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
    extend: {
      success: "166 227 161",
      "success-foreground": "30 30 46",
      warning: "249 226 175",
      "warning-foreground": "30 30 46",
      info: "137 220 235",
      "info-foreground": "30 30 46",
      code: "24 24 37",
      "code-foreground": "205 214 244",
      link: "137 180 250",
      selection: "88 91 112",
    },
  }),
  definePreset({
    name: "tokyo-night",
    label: "Tokyo Night",
    mode: "dark",
    description: "Neon night city: electric blue on deep indigo.",
    core: {
      background: "26 27 38",
      foreground: "192 202 245",
      muted: "36 40 59",
      "muted-foreground": "89 95 119",
      border: "36 40 59",
      primary: "122 162 247",
      "primary-foreground": "26 27 38",
      secondary: "36 40 59",
      "secondary-foreground": "192 202 245",
      destructive: "247 118 142",
      "destructive-foreground": "26 27 38",
      accent: "36 40 59",
      "accent-foreground": "192 202 245",
      card: "31 35 53",
      "card-foreground": "192 202 245",
      popover: "31 35 53",
      "popover-foreground": "192 202 245",
      ring: "122 162 247",
      input: "36 40 59",
    },
    extend: {
      success: "158 206 106",
      "success-foreground": "26 27 38",
      warning: "224 175 104",
      "warning-foreground": "26 27 38",
      info: "125 207 255",
      "info-foreground": "26 27 38",
      sidebar: "22 22 30",
      "sidebar-foreground": "192 202 245",
      "user-message": "36 40 59",
      "user-message-foreground": "192 202 245",
      code: "22 22 30",
      "code-foreground": "192 202 245",
      link: "125 207 255",
      selection: "40 52 87",
    },
  }),
  definePreset({
    name: "onedark",
    label: "One Dark",
    mode: "dark",
    description: "Atom's beloved blue-grey dark with bright accents.",
    core: {
      background: "40 44 52",
      foreground: "171 178 191",
      muted: "44 49 58",
      "muted-foreground": "92 99 112",
      border: "44 49 58",
      primary: "97 175 239",
      "primary-foreground": "40 44 52",
      secondary: "44 49 58",
      "secondary-foreground": "171 178 191",
      destructive: "224 108 117",
      "destructive-foreground": "40 44 52",
      accent: "44 49 58",
      "accent-foreground": "171 178 191",
      card: "40 44 52",
      "card-foreground": "171 178 191",
      popover: "40 44 52",
      "popover-foreground": "171 178 191",
      ring: "97 175 239",
      input: "44 49 58",
    },
    extend: {
      success: "152 195 121",
      "success-foreground": "40 44 52",
      warning: "229 192 123",
      "warning-foreground": "40 44 52",
      info: "86 182 194",
      "info-foreground": "40 44 52",
      code: "33 37 43",
      "code-foreground": "171 178 191",
      link: "97 175 239",
      selection: "62 68 81",
    },
  }),
  definePreset({
    name: "gruvbox-dark",
    label: "Gruvbox Dark",
    mode: "dark",
    description: "Retro-groove warm contrast, easy on the eyes.",
    core: {
      background: "40 40 40",
      foreground: "235 219 178",
      muted: "60 56 54",
      "muted-foreground": "146 131 116",
      border: "60 56 54",
      primary: "250 189 47",
      "primary-foreground": "40 40 40",
      secondary: "60 56 54",
      "secondary-foreground": "235 219 178",
      destructive: "251 73 52",
      "destructive-foreground": "40 40 40",
      accent: "60 56 54",
      "accent-foreground": "235 219 178",
      card: "40 40 40",
      "card-foreground": "235 219 178",
      popover: "40 40 40",
      "popover-foreground": "235 219 178",
      ring: "250 189 47",
      input: "60 56 54",
    },
    extend: {
      success: "184 187 38",
      "success-foreground": "40 40 40",
      warning: "254 128 25",
      "warning-foreground": "40 40 40",
      info: "131 165 152",
      "info-foreground": "40 40 40",
      code: "29 32 33",
      "code-foreground": "235 219 178",
      link: "131 165 152",
      selection: "80 73 69",
    },
  }),
  definePreset({
    name: "monokai",
    label: "Monokai",
    mode: "dark",
    description: "High-voltage pink, green and orange on graphite.",
    core: {
      background: "39 40 34",
      foreground: "248 248 242",
      muted: "62 61 50",
      "muted-foreground": "117 113 94",
      border: "62 61 50",
      primary: "166 226 46",
      "primary-foreground": "39 40 34",
      secondary: "62 61 50",
      "secondary-foreground": "248 248 242",
      destructive: "249 38 114",
      "destructive-foreground": "248 248 242",
      accent: "62 61 50",
      "accent-foreground": "248 248 242",
      card: "39 40 34",
      "card-foreground": "248 248 242",
      popover: "39 40 34",
      "popover-foreground": "248 248 242",
      ring: "166 226 46",
      input: "62 61 50",
    },
    extend: {
      success: "166 226 46",
      "success-foreground": "39 40 34",
      warning: "253 151 31",
      "warning-foreground": "39 40 34",
      info: "102 217 239",
      "info-foreground": "39 40 34",
      code: "30 31 28",
      "code-foreground": "248 248 242",
      link: "102 217 239",
      selection: "73 72 62",
    },
  }),
  definePreset({
    name: "rose-pine",
    label: "Rosé Pine",
    mode: "dark",
    description: "Rosé Pine main: muted rose and iris on deep plum.",
    core: {
      background: "25 23 36",
      foreground: "224 222 244",
      muted: "31 29 46",
      "muted-foreground": "110 106 134",
      border: "38 35 58",
      primary: "196 167 231",
      "primary-foreground": "25 23 36",
      secondary: "31 29 46",
      "secondary-foreground": "224 222 244",
      destructive: "235 111 146",
      "destructive-foreground": "25 23 36",
      accent: "31 29 46",
      "accent-foreground": "224 222 244",
      card: "25 23 36",
      "card-foreground": "224 222 244",
      popover: "25 23 36",
      "popover-foreground": "224 222 244",
      ring: "196 167 231",
      input: "38 35 58",
    },
    extend: {
      success: "156 207 216",
      "success-foreground": "25 23 36",
      warning: "246 193 119",
      "warning-foreground": "25 23 36",
      info: "156 207 216",
      "info-foreground": "25 23 36",
      sidebar: "31 29 46",
      "sidebar-foreground": "224 222 244",
      code: "31 29 46",
      "code-foreground": "224 222 244",
      link: "156 207 216",
      selection: "64 61 82",
    },
  }),
  definePreset({
    name: "everforest-dark",
    label: "Everforest",
    mode: "dark",
    description: "Everforest dark: soft greens in a misty forest.",
    core: {
      background: "45 53 59",
      foreground: "211 198 170",
      muted: "52 63 68",
      "muted-foreground": "127 137 125",
      border: "52 63 68",
      primary: "167 192 128",
      "primary-foreground": "45 53 59",
      secondary: "52 63 68",
      "secondary-foreground": "211 198 170",
      destructive: "230 126 128",
      "destructive-foreground": "45 53 59",
      accent: "52 63 68",
      "accent-foreground": "211 198 170",
      card: "45 53 59",
      "card-foreground": "211 198 170",
      popover: "45 53 59",
      "popover-foreground": "211 198 170",
      ring: "167 192 128",
      input: "52 63 68",
    },
    extend: {
      success: "167 192 128",
      "success-foreground": "45 53 59",
      warning: "219 188 127",
      "warning-foreground": "45 53 59",
      info: "127 187 179",
      "info-foreground": "45 53 59",
      code: "35 42 46",
      "code-foreground": "211 198 170",
      link: "127 187 179",
      selection: "61 72 77",
    },
  }),
  definePreset({
    name: "kanagawa",
    label: "Kanagawa",
    mode: "dark",
    description: "Kanagawa wave: ink blues with dragon-fire accents.",
    core: {
      background: "31 31 40",
      foreground: "220 215 186",
      muted: "42 42 55",
      "muted-foreground": "114 113 105",
      border: "42 42 55",
      primary: "126 156 216",
      "primary-foreground": "31 31 40",
      secondary: "42 42 55",
      "secondary-foreground": "220 215 186",
      destructive: "228 104 118",
      "destructive-foreground": "31 31 40",
      accent: "42 42 55",
      "accent-foreground": "220 215 186",
      card: "31 31 40",
      "card-foreground": "220 215 186",
      popover: "31 31 40",
      "popover-foreground": "220 215 186",
      ring: "126 156 216",
      input: "42 42 55",
    },
    extend: {
      success: "152 187 108",
      "success-foreground": "31 31 40",
      warning: "255 158 59",
      "warning-foreground": "31 31 40",
      info: "127 180 202",
      "info-foreground": "31 31 40",
      sidebar: "22 22 29",
      "sidebar-foreground": "220 215 186",
      code: "22 22 29",
      "code-foreground": "220 215 186",
      link: "127 180 202",
      selection: "45 79 103",
    },
  }),
  definePreset({
    name: "solarized-dark",
    label: "Solarized Dark",
    mode: "dark",
    description: "Precision teal dark with selective yellow accents.",
    core: {
      background: "0 43 54",
      foreground: "131 148 150",
      muted: "7 54 66",
      "muted-foreground": "88 110 117",
      border: "7 54 66",
      primary: "38 139 210",
      "primary-foreground": "253 246 227",
      secondary: "7 54 66",
      "secondary-foreground": "131 148 150",
      destructive: "220 50 47",
      "destructive-foreground": "253 246 227",
      accent: "7 54 66",
      "accent-foreground": "131 148 150",
      card: "0 43 54",
      "card-foreground": "131 148 150",
      popover: "0 43 54",
      "popover-foreground": "131 148 150",
      ring: "38 139 210",
      input: "7 54 66",
    },
    extend: {
      success: "133 153 0",
      "success-foreground": "253 246 227",
      warning: "181 137 0",
      "warning-foreground": "253 246 227",
      info: "42 161 152",
      "info-foreground": "0 43 54",
      code: "7 54 66",
      "code-foreground": "131 148 150",
      link: "38 139 210",
      selection: "7 54 66",
    },
  }),
  definePreset({
    name: "github-dark",
    label: "GitHub Dark",
    mode: "dark",
    description: "Familiar GitHub dimmed dark with blue highlights.",
    core: {
      background: "13 17 23",
      foreground: "230 237 243",
      muted: "22 27 34",
      "muted-foreground": "125 133 144",
      border: "48 54 61",
      primary: "68 147 248",
      "primary-foreground": "255 255 255",
      secondary: "22 27 34",
      "secondary-foreground": "230 237 243",
      destructive: "248 81 73",
      "destructive-foreground": "13 17 23",
      accent: "22 27 34",
      "accent-foreground": "230 237 243",
      card: "22 27 34",
      "card-foreground": "230 237 243",
      popover: "22 27 34",
      "popover-foreground": "230 237 243",
      ring: "68 147 248",
      input: "48 54 61",
    },
    extend: {
      success: "63 185 80",
      "success-foreground": "13 17 23",
      warning: "210 153 34",
      "warning-foreground": "13 17 23",
      info: "88 166 255",
      "info-foreground": "13 17 23",
      sidebar: "13 17 23",
      "sidebar-foreground": "230 237 243",
      code: "22 27 34",
      "code-foreground": "230 237 243",
      link: "68 147 248",
      selection: "31 111 235",
    },
  }),
  definePreset({
    name: "midnight",
    label: "Midnight",
    mode: "dark",
    description: "Original deep-navy night with cool blue glow.",
    core: {
      background: "10 15 30",
      foreground: "219 228 255",
      muted: "19 26 48",
      "muted-foreground": "139 148 179",
      border: "19 26 48",
      primary: "110 168 254",
      "primary-foreground": "10 15 30",
      secondary: "19 26 48",
      "secondary-foreground": "219 228 255",
      destructive: "255 107 107",
      "destructive-foreground": "10 15 30",
      accent: "19 26 48",
      "accent-foreground": "219 228 255",
      card: "19 26 48",
      "card-foreground": "219 228 255",
      popover: "19 26 48",
      "popover-foreground": "219 228 255",
      ring: "110 168 254",
      input: "19 26 48",
    },
    extend: {
      success: "81 207 102",
      "success-foreground": "10 15 30",
      warning: "252 196 25",
      "warning-foreground": "10 15 30",
      info: "102 217 232",
      "info-foreground": "10 15 30",
      sidebar: "6 10 23",
      "sidebar-foreground": "219 228 255",
      code: "6 10 23",
      "code-foreground": "219 228 255",
      link: "110 168 254",
      selection: "30 44 85",
    },
  }),
  definePreset({
    name: "solarized-light",
    label: "Solarized Light",
    mode: "light",
    description: "Warm paper light with the classic solarized accents.",
    core: {
      background: "253 246 227",
      foreground: "101 123 131",
      muted: "238 232 213",
      "muted-foreground": "147 161 161",
      border: "238 232 213",
      primary: "38 139 210",
      "primary-foreground": "253 246 227",
      secondary: "238 232 213",
      "secondary-foreground": "101 123 131",
      destructive: "220 50 47",
      "destructive-foreground": "253 246 227",
      accent: "238 232 213",
      "accent-foreground": "101 123 131",
      card: "253 246 227",
      "card-foreground": "101 123 131",
      popover: "253 246 227",
      "popover-foreground": "101 123 131",
      ring: "38 139 210",
      input: "238 232 213",
    },
    extend: {
      success: "133 153 0",
      "success-foreground": "253 246 227",
      warning: "181 137 0",
      "warning-foreground": "253 246 227",
      info: "42 161 152",
      "info-foreground": "253 246 227",
      code: "0 43 54",
      "code-foreground": "131 148 150",
      link: "38 139 210",
      selection: "220 208 176",
    },
  }),
  definePreset({
    name: "gruvbox-light",
    label: "Gruvbox Light",
    mode: "light",
    description: "Warm parchment light with earthy retro accents.",
    core: {
      background: "251 241 199",
      foreground: "60 56 54",
      muted: "235 219 178",
      "muted-foreground": "146 131 116",
      border: "235 219 178",
      primary: "175 58 3",
      "primary-foreground": "251 241 199",
      secondary: "235 219 178",
      "secondary-foreground": "60 56 54",
      destructive: "157 0 6",
      "destructive-foreground": "251 241 199",
      accent: "235 219 178",
      "accent-foreground": "60 56 54",
      card: "251 241 199",
      "card-foreground": "60 56 54",
      popover: "251 241 199",
      "popover-foreground": "60 56 54",
      ring: "175 58 3",
      input: "235 219 178",
    },
    extend: {
      success: "121 116 14",
      "success-foreground": "251 241 199",
      warning: "181 118 20",
      "warning-foreground": "251 241 199",
      info: "7 102 120",
      "info-foreground": "251 241 199",
      code: "60 56 54",
      "code-foreground": "235 219 178",
      link: "7 102 120",
      selection: "213 196 161",
    },
  }),
  definePreset({
    name: "github-light",
    label: "GitHub Light",
    mode: "light",
    description: "Crisp GitHub light with familiar blue links.",
    core: {
      background: "255 255 255",
      foreground: "31 35 40",
      muted: "246 248 250",
      "muted-foreground": "89 99 110",
      border: "209 217 224",
      primary: "9 105 218",
      "primary-foreground": "255 255 255",
      secondary: "246 248 250",
      "secondary-foreground": "31 35 40",
      destructive: "209 36 47",
      "destructive-foreground": "255 255 255",
      accent: "246 248 250",
      "accent-foreground": "31 35 40",
      card: "255 255 255",
      "card-foreground": "31 35 40",
      popover: "255 255 255",
      "popover-foreground": "31 35 40",
      ring: "9 105 218",
      input: "209 217 224",
    },
    extend: {
      success: "26 127 55",
      "success-foreground": "255 255 255",
      warning: "154 103 0",
      "warning-foreground": "255 255 255",
      info: "9 105 218",
      "info-foreground": "255 255 255",
      code: "246 248 250",
      "code-foreground": "31 35 40",
      link: "9 105 218",
      selection: "182 227 255",
    },
  }),
  definePreset({
    name: "rose-pine-dawn",
    label: "Rosé Dawn",
    mode: "light",
    description: "Rosé Pine dawn: soft rose morning light.",
    core: {
      background: "250 244 237",
      foreground: "87 82 121",
      muted: "242 233 225",
      "muted-foreground": "121 117 147",
      border: "223 218 217",
      primary: "144 122 169",
      "primary-foreground": "250 244 237",
      secondary: "242 233 225",
      "secondary-foreground": "87 82 121",
      destructive: "180 99 122",
      "destructive-foreground": "250 244 237",
      accent: "242 233 225",
      "accent-foreground": "87 82 121",
      card: "250 244 237",
      "card-foreground": "87 82 121",
      popover: "250 244 237",
      "popover-foreground": "87 82 121",
      ring: "144 122 169",
      input: "223 218 217",
    },
    extend: {
      success: "40 105 131",
      "success-foreground": "250 244 237",
      warning: "234 157 52",
      "warning-foreground": "87 82 121",
      info: "86 148 159",
      "info-foreground": "250 244 237",
      code: "242 233 225",
      "code-foreground": "87 82 121",
      link: "144 122 169",
      selection: "223 218 217",
    },
  }),
  definePreset({
    name: "everforest-light",
    label: "Everforest Light",
    mode: "light",
    description: "Everforest light: warm paper with mossy greens.",
    core: {
      background: "247 243 232",
      foreground: "92 106 114",
      muted: "237 229 211",
      "muted-foreground": "147 159 145",
      border: "237 229 211",
      primary: "141 161 1",
      "primary-foreground": "247 243 232",
      secondary: "237 229 211",
      "secondary-foreground": "92 106 114",
      destructive: "248 85 82",
      "destructive-foreground": "247 243 232",
      accent: "237 229 211",
      "accent-foreground": "92 106 114",
      card: "247 243 232",
      "card-foreground": "92 106 114",
      popover: "247 243 232",
      "popover-foreground": "92 106 114",
      ring: "141 161 1",
      input: "237 229 211",
    },
    extend: {
      success: "106 134 0",
      "success-foreground": "247 243 232",
      warning: "223 160 0",
      "warning-foreground": "92 106 114",
      info: "58 148 197",
      "info-foreground": "247 243 232",
      code: "45 53 59",
      "code-foreground": "211 198 170",
      link: "58 148 197",
      selection: "224 213 187",
    },
  }),
  definePreset({
    name: "carbon",
    label: "Carbon",
    mode: "dark",
    description: "True-black OLED: pure #000 canvas, vivid periwinkle accents.",
    core: {
      background: "0 0 0",
      foreground: "237 240 245",
      muted: "18 18 20",
      "muted-foreground": "150 155 165",
      border: "30 30 34",
      primary: "99 131 255",
      "primary-foreground": "6 8 16",
      secondary: "18 18 20",
      "secondary-foreground": "237 240 245",
      destructive: "248 113 113",
      "destructive-foreground": "8 8 10",
      accent: "18 18 20",
      "accent-foreground": "237 240 245",
      card: "12 12 14",
      "card-foreground": "237 240 245",
      popover: "18 18 22",
      "popover-foreground": "237 240 245",
      ring: "124 152 255",
      input: "30 30 34",
    },
    extend: {
      success: "74 222 128",
      "success-foreground": "4 10 6",
      warning: "250 204 21",
      "warning-foreground": "10 8 0",
      info: "125 211 252",
      "info-foreground": "4 10 14",
      sidebar: "8 8 10",
      "sidebar-foreground": "237 240 245",
      topbar: "10 10 12",
      "topbar-foreground": "237 240 245",
      "user-message": "24 24 28",
      "user-message-foreground": "237 240 245",
      "assistant-message": "14 14 16",
      "assistant-message-foreground": "237 240 245",
      code: "10 10 12",
      "code-foreground": "210 214 222",
      link: "124 152 255",
      selection: "40 44 60",
    },
  }),
  definePreset({
    name: "nebula",
    label: "Nebula",
    mode: "dark",
    description: "Deep indigo-violet with magenta and cyan aurora accents.",
    core: {
      background: "18 14 30",
      foreground: "234 230 250",
      muted: "28 22 46",
      "muted-foreground": "165 158 195",
      border: "40 32 64",
      primary: "167 139 250",
      "primary-foreground": "14 10 26",
      secondary: "28 22 46",
      "secondary-foreground": "234 230 250",
      destructive: "255 107 129",
      "destructive-foreground": "18 10 16",
      accent: "28 22 46",
      "accent-foreground": "234 230 250",
      card: "24 19 40",
      "card-foreground": "234 230 250",
      popover: "28 22 46",
      "popover-foreground": "234 230 250",
      ring: "167 139 250",
      input: "40 32 64",
    },
    extend: {
      success: "94 234 150",
      "success-foreground": "12 20 14",
      warning: "252 196 120",
      "warning-foreground": "24 16 4",
      info: "120 210 255",
      "info-foreground": "8 14 24",
      sidebar: "14 10 24",
      "sidebar-foreground": "234 230 250",
      topbar: "16 12 28",
      "topbar-foreground": "234 230 250",
      "user-message": "32 26 52",
      "user-message-foreground": "234 230 250",
      "assistant-message": "22 17 38",
      "assistant-message-foreground": "234 230 250",
      code: "12 9 22",
      "code-foreground": "214 208 240",
      link: "150 190 255",
      selection: "52 40 88",
    },
  }),
  definePreset({
    name: "ember",
    label: "Ember",
    mode: "dark",
    description: "Warm graphite: neutral-warm near-black with amber embers.",
    core: {
      background: "26 24 22",
      foreground: "240 234 226",
      muted: "38 34 30",
      "muted-foreground": "170 160 148",
      border: "44 40 35",
      primary: "233 166 84",
      "primary-foreground": "24 16 4",
      secondary: "38 34 30",
      "secondary-foreground": "240 234 226",
      destructive: "245 120 100",
      "destructive-foreground": "20 8 4",
      accent: "38 34 30",
      "accent-foreground": "240 234 226",
      card: "30 27 24",
      "card-foreground": "240 234 226",
      popover: "34 30 26",
      "popover-foreground": "240 234 226",
      ring: "233 166 84",
      input: "44 40 35",
    },
    extend: {
      success: "140 200 110",
      "success-foreground": "16 24 8",
      warning: "240 190 90",
      "warning-foreground": "24 16 2",
      info: "120 180 220",
      "info-foreground": "8 16 24",
      sidebar: "20 18 16",
      "sidebar-foreground": "240 234 226",
      topbar: "24 21 18",
      "topbar-foreground": "240 234 226",
      "user-message": "40 36 31",
      "user-message-foreground": "240 234 226",
      "assistant-message": "28 25 22",
      "assistant-message-foreground": "240 234 226",
      code: "18 16 14",
      "code-foreground": "220 212 200",
      link: "220 170 110",
      selection: "56 50 42",
    },
  }),
  definePreset({
    name: "night-owl",
    label: "Night Owl",
    mode: "dark",
    description: "Deep-blue dev-tool classic with vivid teal accents.",
    core: {
      background: "1 22 39",
      foreground: "214 222 235",
      muted: "10 34 55",
      "muted-foreground": "130 165 200",
      border: "10 34 55",
      primary: "130 219 202",
      "primary-foreground": "1 22 39",
      secondary: "10 34 55",
      "secondary-foreground": "214 222 235",
      destructive: "239 83 80",
      "destructive-foreground": "1 22 39",
      accent: "10 34 55",
      "accent-foreground": "214 222 235",
      card: "1 22 39",
      "card-foreground": "214 222 235",
      popover: "10 34 55",
      "popover-foreground": "214 222 235",
      ring: "130 219 202",
      input: "10 34 55",
    },
    extend: {
      success: "195 232 141",
      "success-foreground": "1 22 39",
      warning: "247 140 108",
      "warning-foreground": "1 22 39",
      info: "137 221 255",
      "info-foreground": "1 22 39",
      sidebar: "0 17 30",
      "sidebar-foreground": "214 222 235",
      "user-message": "10 34 55",
      "user-message-foreground": "214 222 235",
      code: "0 15 28",
      "code-foreground": "214 222 235",
      link: "125 200 255",
      selection: "21 58 88",
    },
  }),
  definePreset({
    name: "ayu-mirage",
    label: "Ayu Mirage",
    mode: "dark",
    description: "Refined muted blue-grey with a golden accent.",
    core: {
      background: "31 36 48",
      foreground: "203 204 198",
      muted: "37 44 62",
      "muted-foreground": "145 160 180",
      border: "37 44 62",
      primary: "255 214 99",
      "primary-foreground": "31 36 48",
      secondary: "37 44 62",
      "secondary-foreground": "203 204 198",
      destructive: "242 135 121",
      "destructive-foreground": "31 36 48",
      accent: "37 44 62",
      "accent-foreground": "203 204 198",
      card: "31 36 48",
      "card-foreground": "203 204 198",
      popover: "37 44 62",
      "popover-foreground": "203 204 198",
      ring: "255 214 99",
      input: "37 44 62",
    },
    extend: {
      success: "170 217 76",
      "success-foreground": "31 36 48",
      warning: "255 203 107",
      "warning-foreground": "31 36 48",
      info: "57 186 230",
      "info-foreground": "31 36 48",
      sidebar: "25 29 40",
      "sidebar-foreground": "203 204 198",
      code: "25 29 40",
      "code-foreground": "203 204 198",
      link: "95 215 255",
      selection: "65 75 100",
    },
  }),
  definePreset({
    name: "poimandres",
    label: "Poimandres",
    mode: "dark",
    description: "Modern deep teal with a mint glow.",
    core: {
      background: "27 30 40",
      foreground: "228 240 251",
      muted: "37 42 58",
      "muted-foreground": "150 158 185",
      border: "37 42 58",
      primary: "93 228 199",
      "primary-foreground": "27 30 40",
      secondary: "37 42 58",
      "secondary-foreground": "228 240 251",
      destructive: "208 103 157",
      "destructive-foreground": "27 30 40",
      accent: "37 42 58",
      "accent-foreground": "228 240 251",
      card: "27 30 40",
      "card-foreground": "228 240 251",
      popover: "37 42 58",
      "popover-foreground": "228 240 251",
      ring: "93 228 199",
      input: "37 42 58",
    },
    extend: {
      success: "139 233 178",
      "success-foreground": "27 30 40",
      warning: "240 205 130",
      "warning-foreground": "27 30 40",
      info: "137 221 255",
      "info-foreground": "27 30 40",
      sidebar: "21 24 33",
      "sidebar-foreground": "228 240 251",
      "user-message": "37 42 58",
      "user-message-foreground": "228 240 251",
      code: "21 24 33",
      "code-foreground": "228 240 251",
      link: "137 221 255",
      selection: "48 58 84",
    },
  }),
  definePreset({
    name: "flexoki-dark",
    label: "Flexoki Dark",
    mode: "dark",
    description: "Warm paper-dark ink with tuneful earthy accents.",
    core: {
      background: "16 15 15",
      foreground: "206 205 195",
      muted: "28 27 26",
      "muted-foreground": "135 133 128",
      border: "52 51 49",
      primary: "58 169 158",
      "primary-foreground": "16 15 15",
      secondary: "28 27 26",
      "secondary-foreground": "206 205 195",
      destructive: "224 95 80",
      "destructive-foreground": "16 15 15",
      accent: "28 27 26",
      "accent-foreground": "206 205 195",
      card: "28 27 26",
      "card-foreground": "206 205 195",
      popover: "28 27 26",
      "popover-foreground": "206 205 195",
      ring: "58 169 158",
      input: "52 51 49",
    },
    extend: {
      success: "135 154 57",
      "success-foreground": "16 15 15",
      warning: "208 162 21",
      "warning-foreground": "16 15 15",
      info: "67 133 190",
      "info-foreground": "16 15 15",
      sidebar: "16 15 15",
      "sidebar-foreground": "206 205 195",
      topbar: "22 21 20",
      "topbar-foreground": "206 205 195",
      "user-message": "28 27 26",
      "user-message-foreground": "206 205 195",
      code: "28 27 26",
      "code-foreground": "230 228 217",
      link: "67 133 190",
      selection: "64 62 58",
    },
  }),
  definePreset({
    name: "synthwave-84",
    label: "SynthWave '84",
    mode: "dark",
    description: "Neon pink-and-cyan retro glow on deep purple.",
    core: {
      background: "38 35 53",
      foreground: "235 235 242",
      muted: "52 48 74",
      "muted-foreground": "155 145 190",
      border: "52 48 74",
      primary: "54 249 246",
      "primary-foreground": "38 35 53",
      secondary: "52 48 74",
      "secondary-foreground": "235 235 242",
      destructive: "255 81 113",
      "destructive-foreground": "38 35 53",
      accent: "52 48 74",
      "accent-foreground": "235 235 242",
      card: "38 35 53",
      "card-foreground": "235 235 242",
      popover: "52 48 74",
      "popover-foreground": "235 235 242",
      ring: "249 42 173",
      input: "52 48 74",
    },
    extend: {
      success: "114 241 184",
      "success-foreground": "38 35 53",
      warning: "254 222 93",
      "warning-foreground": "38 35 53",
      info: "184 147 255",
      "info-foreground": "38 35 53",
      sidebar: "28 26 42",
      "sidebar-foreground": "235 235 242",
      "user-message": "52 48 74",
      "user-message-foreground": "235 235 242",
      code: "28 26 42",
      "code-foreground": "235 235 242",
      link: "250 90 190",
      selection: "72 60 110",
    },
  }),
  definePreset({
    name: "vesper",
    label: "Vesper",
    mode: "dark",
    description: "Near-black warm minimal with a peach accent.",
    core: {
      background: "16 16 16",
      foreground: "245 245 245",
      muted: "30 30 30",
      "muted-foreground": "170 170 170",
      border: "38 38 38",
      primary: "255 195 150",
      "primary-foreground": "16 16 16",
      secondary: "30 30 30",
      "secondary-foreground": "245 245 245",
      destructive: "255 110 110",
      "destructive-foreground": "16 16 16",
      accent: "30 30 30",
      "accent-foreground": "245 245 245",
      card: "22 22 22",
      "card-foreground": "245 245 245",
      popover: "26 26 26",
      "popover-foreground": "245 245 245",
      ring: "255 195 150",
      input: "38 38 38",
    },
    extend: {
      success: "150 220 150",
      "success-foreground": "16 16 16",
      warning: "255 200 120",
      "warning-foreground": "16 16 16",
      info: "150 200 255",
      "info-foreground": "16 16 16",
      sidebar: "16 16 16",
      "sidebar-foreground": "245 245 245",
      topbar: "20 20 20",
      "topbar-foreground": "245 245 245",
      "user-message": "30 30 30",
      "user-message-foreground": "245 245 245",
      code: "22 22 22",
      "code-foreground": "235 235 235",
      link: "255 195 150",
      selection: "55 50 45",
    },
  }),
] as const;

/** Canonical dark default (user ruling 2026-09-13): true-black OLED. */
export const DEFAULT_PRESET_NAME = "carbon" as const;
export const DEFAULT_LIGHT_PRESET_NAME = "light" as const;
/** Special "follow the OS" selection. Not a real preset -- resolved at apply time. */
export const SYSTEM_PRESET_NAME = "system" as const;
export type PresetName = (typeof PRESETS)[number]["name"] | (typeof SYSTEM_PRESET_NAME);

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

export function presetsByMode(mode: ThemeMode): (PresetTokens & { readonly name: PresetName })[] {
  return PRESETS.filter((p) => p.mode === mode);
}

export function getPresetMode(name: string): ThemeMode {
  return getPreset(name).mode;
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

/** Convert a token map to the CSS string for a `:root[data-theme=...]` block.
 *
 * R4.1 step 1c (Tailwind v4): each `--color-*` variable carries
 * a full ``rgb()`` value (not a bare tuple) so the Tailwind v4
 * utility classes (``bg-background``, ``text-foreground`` etc.)
 * resolve without arbitrary-value wrappers. The preset
 * ``TokenMap`` stores bare tuples (the on-disk + in-memory
 * source of truth); this writer wraps each one.
 */
export function tokensToCssVariables(tokens: TokenMap, indent: string = "  "): string {
  return Object.entries(tokens)
    .map(([name, value]) => `${indent}--color-${name}: rgb(${value});`)
    .join("\n");
}

/**
 * The picker UI uses ``<input type="color">`` which round-trips
 * hex strings (``#rrggbb``). The preset tokens are stored as RGB
 * tuples (``"r g b"``); the custom override map lets the user
 * store either shape. ``rgbTupleToHex`` converts a preset value
 * to the form the picker expects; ``hexToRgbTuple`` converts a
 * picker value back to the RGB-tuple shape when the user picks
 * a color (so the merged map is consistently RGB-tuple, matching
 * Tailwind config + globals.css expectations).
 */
export function rgbTupleToHex(value: string): string {
  // Already hex?
  if (value.startsWith("#")) return value;
  const parts = value.trim().split(/\s+/);
  if (parts.length !== 3) return "#000000";
  const [r, g, b] = parts.map((p) => {
    const n = Number.parseInt(p, 10);
    return Number.isFinite(n) ? Math.max(0, Math.min(255, n)) : 0;
  });
  const toHex = (n: number) => n.toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

export function hexToRgbTuple(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "0 0 0";
  const n = m[1];
  const r = parseInt(n.slice(0, 2), 16);
  const g = parseInt(n.slice(2, 4), 16);
  const b = parseInt(n.slice(4, 6), 16);
  return `${r} ${g} ${b}`;
}
