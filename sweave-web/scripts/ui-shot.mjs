/**
 * UI screenshot harness — runs the Vite DEV server (required because
 * /dev/chat-lab is gated by import.meta.env.DEV and tree-shaken from the
 * production bundle), drives the system Edge (Chromium) headlessly, and
 * captures PNGs into scripts/shots/.
 *
 * Usage:  npm run ui:shot
 * Browser: override with SWEAVE_BROWSER="<path to chrome/edge.exe>".
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE =
  process.env.SWEAVE_BROWSER ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 4173;
const BASE = `http://localhost:${PORT}`;
const OUT = new URL("./shots/", import.meta.url).pathname.replace(/^\/([A-Za-z]):/, "$1:");

mkdirSync(OUT, { recursive: true });

// Dev server (NOT `vite preview` — the chat-lab route is dev-only).
const server = spawn("npx", ["vite", "--port", String(PORT), "--strictPort"], {
  stdio: "ignore",
  shell: true,
  cwd: process.cwd(),
});

// Windows: SIGTERM on the shell wrapper does NOT kill the vite grandchild —
// kill the whole process tree or the server outlives the script (hangs CI).
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

  // 1. App shell (sidebar + topbar + chat empty state).
  await page.goto(`${BASE}/`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2500);
  await page.screenshot({ path: `${OUT}shell.png` });

  // 2. Command palette.
  await page.keyboard.press("Control+k");
  await sleep(700);
  await page.screenshot({ path: `${OUT}command.png` });
  await page.keyboard.press("Escape");
  await sleep(400);

  // 3. Chat lab — REAL-Thread visual test. Captures the resolved
  //    thread, the welcome/empty state, and a mid-stream turn.
  await page.goto(`${BASE}/dev/chat-lab`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2500);
  await page.screenshot({ path: `${OUT}chat-lab.png`, fullPage: true });

  await page.getByTestId("lab-btn-empty").click().catch(() => {});
  await sleep(800);
  await page.screenshot({ path: `${OUT}chat-lab-empty.png` });

  await page.getByTestId("lab-btn-stream").click().catch(() => {});
  await sleep(900);
  await page.screenshot({ path: `${OUT}chat-lab-streaming.png` });
  await sleep(2500);
  await page.screenshot({ path: `${OUT}chat-lab-streamed.png` });

  await browser.close();
  console.log(`SHOT_OK -> ${OUT} (console_errors=${errors.length})`);
  if (errors.length) console.log(errors.slice(0, 8).join("\n"));
} finally {
  killServer();
  await sleep(500);
}
