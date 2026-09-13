/**
 * Font-stack pin.
 *
 * The UI pairs bundled variable webfonts (Fontsource imports in
 * main.tsx) with system fallbacks in globals.css so the first paint
 * never waits on a download (font-display: swap enhances in place).
 * These tests pin the stack order: webfont first, systems after.
 *
 * NOTE: globals.css is read from disk via node:fs (typed by
 * src/test/node-shims.d.ts) because the Tailwind v4 vite plugin
 * resolves `?raw` CSS imports to an empty string in the test
 * pipeline — unlike index.html, which passes through untouched.
 */
import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const CSS = fs.readFileSync(path.resolve(here, "..", "globals.css"), "utf8");

function stackValue(name: string): string {
  const m = new RegExp(`${name}:\\s*([^;]+);`).exec(CSS);
  expect(m, `${name} declared in globals.css`).not.toBeNull();
  return m![1];
}

describe("font stacks", () => {
  it("sans leads with Inter Variable, keeps system fallbacks", () => {
    const stack = stackValue("--font-sans");
    const first = stack.split(",")[0].trim().replace(/["']/g, "");
    expect(first).toBe("Inter Variable");
    for (const fallback of ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto"]) {
      expect(stack).toContain(fallback);
    }
  });

  it("mono leads with JetBrains Mono Variable, keeps system fallbacks", () => {
    const stack = stackValue("--font-mono");
    const first = stack.split(",")[0].trim().replace(/["']/g, "");
    expect(first).toBe("JetBrains Mono Variable");
    for (const fallback of ["ui-monospace", "Menlo", "Cascadia Code"]) {
      expect(stack).toContain(fallback);
    }
  });
});
