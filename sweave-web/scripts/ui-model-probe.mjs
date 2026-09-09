/**
 * ModelPicker visual probe.
 *
 * Opens the Agents page, expands the global backend-specialist's
 * model picker, and captures states: open (full list) + filtered.
 * Never selects an option (that would PUT a new model) — open,
 * filter, screenshot, Escape.
 *
 * Usage:  node scripts/ui-model-probe.mjs
 * Requires the backend on 127.0.0.1:8100.
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE =
  process.env.SWEAVE_BROWSER ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 4177;
const BASE = `http://localhost:${PORT}`;
const OUT = new URL("./shots/", import.meta.url).pathname.replace(/^\/([A-Za-z]):/, "$1:");
const TRIGGER = "model-picker-global-backend-specialist";

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

/** offsetParent visibility (not just classList) per the UI regression pattern. */
async function isShown(locator) {
  return locator.evaluate(
    (el) => !!el.offsetParent && getComputedStyle(el).visibility !== "hidden",
  ).catch(() => false);
}

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? "PASS" : "FAIL"} ${name}`);
  if (!cond) failures += 1;
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

  await page.goto(`${BASE}/agents`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2500);

  const trigger = page.getByTestId(TRIGGER);
  check("picker trigger present", (await trigger.count()) > 0);
  await trigger.click().catch(() => {});
  await sleep(1000);

  const search = page.getByTestId(`${TRIGGER}-search`);
  check("search field visible (offsetParent)", await isShown(search));
  await page.screenshot({ path: `${OUT}model-open.png` });

  const countBefore = await page.getByTestId(`${TRIGGER}-count`).textContent().catch(() => "");
  await search.fill("glm");
  await sleep(500);
  const countAfter = await page.getByTestId(`${TRIGGER}-count`).textContent().catch(() => "");
  check(`filter narrows list (${JSON.stringify(countBefore)} -> ${JSON.stringify(countAfter)})`, countBefore !== countAfter);
  const glmRow = page.getByTestId(`${TRIGGER}-list`).getByRole("option", { name: /glm/i }).first();
  check("filtered glm option visible (offsetParent)", await isShown(glmRow));
  await page.screenshot({ path: `${OUT}model-filtered.png` });

  await page.keyboard.press("Escape");
  await sleep(400);
  check("panel closes on Escape", !(await isShown(search)));

  await browser.close();
  console.log(`MODEL_PROBE_OK -> ${OUT} (console_errors=${errors.length})`);
  if (errors.length) console.log(errors.slice(0, 8).join("\n"));
  if (failures) {
    console.log(`MODEL_PROBE_FAILURES=${failures}`);
    process.exitCode = 1;
  }
} finally {
  killServer();
  await sleep(500);
}
