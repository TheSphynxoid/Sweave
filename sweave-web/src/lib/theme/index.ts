/**
 * Public re-exports for the design system.
 *
 * Usage:
 *   import { PRESETS, resolveThemeTokens, applyThemeToDocument } from "@/lib/theme";
 */

export {
  PRESETS,
  DEFAULT_PRESET_NAME,
  DEFAULT_LIGHT_PRESET_NAME,
  SYSTEM_PRESET_NAME,
  ALL_TOKEN_NAMES,
  TOKEN_GROUPS,
  TOKEN_LABELS,
  type PresetName,
  type ThemeMode,
  type TokenName,
  type CoreTokenName,
  type ExtendedTokenName,
  type TokenMap,
  type TokenGroup,
  type PresetTokens,
  type CustomOverride,
  getPreset,
  getPresetMode,
  isTokenName,
  listPresetNames,
  presetsByMode,
  resolveTokens,
  sanitizeCustomOverride,
  tokensToCssVariables,
  rgbTupleToHex,
  hexToRgbTuple,
} from "./tokens";

export {
  type ActiveTheme,
  type ChatBackdrop,
  defaultActiveTheme,
  loadActiveTheme,
  saveActiveTheme,
  resolveThemeTokens,
  applyThemeToDocument,
  setActiveTheme,
  prefersDark,
  resolveSystemPresetName,
  THEME_DATA_ATTR,
  DEFAULT_BACKDROP,
  listBackdrops,
  loadBackdrop,
  saveBackdrop,
  applyBackdropToDocument,
  CHAT_BACKDROP_ATTR,
} from "./switcher";

export { ThemeProvider, useTheme } from "./ThemeProvider";
