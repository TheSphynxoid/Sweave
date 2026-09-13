/**
 * No-FOUC inline script pin.
 *
 * `index.html` embeds a generated copy of the preset table (+ font-scale
 * map) so the first paint already uses the stored theme. The generator
 * is `scripts/gen-theme-inline.mjs` — re-run it after touching
 * tokens.ts / fontScale.ts. These tests fail on drift, so a stale
 * inline block can never pass the gate silently.
 */
import { describe, it, expect } from "vitest";
import { PRESETS, DEFAULT_PRESET_NAME, DEFAULT_LIGHT_PRESET_NAME } from "../tokens";
import {
  FONT_SCALE_OPTIONS,
  DEFAULT_FONT_SCALE_ID,
  FONT_SCALE_STORAGE_KEY,
} from "../fontScale";
// Raw-HTML import (vite/vitest feature; typed via src/vite-env.d.ts).
import INDEX_HTML from "../../../../index.html?raw";

function readInlineScript(): string {
  const html = INDEX_HTML as string;
  const start = html.indexOf("<!-- SWEAVE-THEME-INLINE:START -->");
  const end = html.indexOf("<!-- SWEAVE-THEME-INLINE:END -->");
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  const block = html.slice(start, end);
  expect(block).toContain("<script>");
  expect(block).toContain("</script>");
  return block;
}

describe("no-FOUC inline script", () => {
  it("embeds every preset's mode + tokens exactly as tokens.ts defines them", () => {
    const script = readInlineScript();
    const m = /var PRESETS=(\{.*?\});\nvar DEFAULT_DARK/s.exec(script);
    expect(m).not.toBeNull();
    const embedded = JSON.parse(m![1]) as Record<
      string,
      { mode: string; tokens: Record<string, string> }
    >;
    expect(Object.keys(embedded).sort()).toEqual(PRESETS.map((p) => p.name).sort());
    for (const preset of PRESETS) {
      expect(embedded[preset.name].mode).toBe(preset.mode);
      expect(embedded[preset.name].tokens).toEqual({ ...preset.tokens });
    }
  });

  it("embeds the live dark/light defaults", () => {
    const script = readInlineScript();
    expect(script).toContain(`var DEFAULT_DARK="${DEFAULT_PRESET_NAME}"`);
    expect(script).toContain(`DEFAULT_LIGHT="${DEFAULT_LIGHT_PRESET_NAME}"`);
  });

  it("embeds the live font-scale map, default, and storage key", () => {
    const script = readInlineScript();
    const expected: Record<string, number> = {};
    for (const o of FONT_SCALE_OPTIONS) expected[o.id] = o.multiplier;
    const m = /var SCALES=(\{.*?\}),DEFAULT_SCALE/s.exec(script);
    expect(m).not.toBeNull();
    expect(JSON.parse(m![1])).toEqual(expected);
    expect(script).toContain(`DEFAULT_SCALE="${DEFAULT_FONT_SCALE_ID}"`);
    expect(script).toContain(`FONT_SCALE_KEY="${FONT_SCALE_STORAGE_KEY}"`);
  });

  it("reads the same localStorage keys the runtime uses", () => {
    const script = readInlineScript();
    expect(script).toContain('THEME_PRESET_KEY="sweave.theme.preset"');
    expect(script).toContain('THEME_CUSTOM_KEY="sweave.theme.custom"');
  });

  it("contains no premature closing script tag (it would leak JS as page text)", () => {
    const script = readInlineScript();
    const body = script
      .replace("<script>", "")
      .replace(/<\/script>\s*$/, "");
    expect(body.toLowerCase()).not.toContain("</script");
  });
});
