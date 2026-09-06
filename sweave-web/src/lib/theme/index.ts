/**
 * Public re-exports for the design system.
 *
 * Usage:
 *   import { PRESETS, resolveThemeTokens, applyThemeToDocument } from "@/lib/theme";
 */

export {
  PRESETS,
  DEFAULT_PRESET_NAME,
  type PresetName,
  type TokenName,
  type TokenMap,
  type PresetTokens,
  type CustomOverride,
  getPreset,
  listPresetNames,
  resolveTokens,
  tokensToCssVariables,
  rgbTupleToHex,
  hexToRgbTuple,
} from "./tokens";

export {
  type ActiveTheme,
  defaultActiveTheme,
  loadActiveTheme,
  saveActiveTheme,
  resolveThemeTokens,
  applyThemeToDocument,
  setActiveTheme,
  THEME_DATA_ATTR,
} from "./switcher";
