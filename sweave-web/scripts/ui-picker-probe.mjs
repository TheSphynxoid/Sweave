/**
 * Project-creation dialog + PathPicker visual probe.
 *
 * Opens the create-project dialog via the sidebar's FolderPlus, then
 * the PathPicker popover, and captures states: browsing C:\, filtered,
 * and a deep path (breadcrumbs + typed path + drive chips).
 *
 * Usage:  node scripts/ui-picker-probe.mjs
 * Requires the backend on 127.0.0.1:8100.
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE =
  process.env.SWEAVE_BROWSER ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 4176;
const BASE = `http://localhost:${PORT}`;
const OUT = new URL("./shots/", import.meta.url).pathname.replace(/^\/([A-Za-z]):/, "$1:");

mkdirSync(OUT, { recursive: true });

const server = spawn("npx", ["vite", "--port", String(PORT), "--strictPort"], {
  stdio: "ignore",
  shell: true,
  cwd: process.cwd(),
});
function killServer() {
  if (!server.pid) return;
  try {
    spawn("taskkill", ["/PID", String(server.pid), "/T", "/F"], {
      stdio: "ignore",
      shell: true,
    });
  } catch {
    /* best effort */
  }
}
process.on("exit", killServer);
process.on("SIGINT", () => process.exit(1));

async function waitUp() {
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(BASE);
      if (r.ok) return;
    } catch {}
    await sleep(500);
  }
  throw new Error("dev server did not start");
}

try {
  await waitUp();
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`${BASE}/`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2500);

  // Open the create-project dialog (sidebar FolderPlus button).
  await page.locator('[aria-label="Create project"]').first().click().catch(() => {});
  await sleep(800);
  await page.screenshot({ path: `${OUT}picker-dialog.png` });

  // Open the picker.
  await page.getByTestId("path-picker-trigger").click().catch(() => {});
  await sleep(1200);
  await page.screenshot({ path: `${OUT}picker-root.png` });

  // Navigate into a folder + filter.
  const firstRow = page.locator('[data-testid="path-picker-list"] button').first();
  if (await firstRow.count()) {
    await firstRow.click();
    await sleep(900);
  }
  const filter = page.getByTestId("path-picker-filter");
  if (await filter.count()) {
    await filter.fill("s");
    await sleep(400);
    await page.screenshot({ path: `${OUT}picker-filtered.png` });
  }

  // Deep path via the typed input (breadcrumbs + up become visible).
  const input = page.getByTestId("path-picker-input");
  if (await input.count()) {
    await input.fill("C:\\Users");
    await input.press("Enter");
    await sleep(900);
    await page.screenshot({ path: `${OUT}picker-deep.png` });
  }

  await browser.close();
  console.log(`PICKER_PROBE_OK -> ${OUT} (console_errors=${errors.length})`);
  if (errors.length) console.log(errors.slice(0, 8).join("\n"));
} finally {
  killServer();
  await sleep(500);
}
