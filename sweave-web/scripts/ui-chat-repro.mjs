/**
 * Chat-turn reproduction probe (duplication + streaming indicator).
 *
 * Drives the REAL app on a REAL session: sends one message through the
 * composer, records every POST + WS frame, samples the DOM row counts
 * while the turn runs, then dumps the backend's persisted message list.
 *
 * Usage:  node scripts/ui-chat-repro.mjs [sessionId] [message]
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
const SESSION_ID = process.argv[2] ?? "final_test-20260908-145152-812abb";
const MESSAGE = process.argv[3] ?? "Repro probe: say OK and nothing else.";
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
    spawn("taskkill", ["/PID", String(server.pid), "/T", "/F"], { stdio: "ignore", shell: true });
  } catch {}
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

  const posts = [];
  const wsEvents = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/messages")) {
      posts.push({ url: req.url(), body: req.postData()?.slice(0, 120) });
    }
  });

  await page.goto(`${BASE}/`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2500);

  // Activate the session via the sidebar tree.
  const projName = SESSION_ID.replace(/-\d{8}-\d{6}-[0-9a-f]+$/, "");
  const projRow = page.locator('[data-testid="project-session-tree"] button', { hasText: projName });
  await projRow.first().click().catch(() => {});
  await sleep(1000);
  await page.locator(`[data-testid="session-tree-item-${SESSION_ID}"]`).click().catch(() => {});
  await sleep(1500);

  const before = await page.evaluate(async (sid) => {
    const d = await fetch(`/api/sessions/${sid}`).then((r) => r.json());
    return d.messages.length;
  }, SESSION_ID).catch(() => -1);
  console.log(`messages before: ${before}`);

  // Send the message through the composer (Enter).
  const input = page.getByTestId("chat-composer-input");
  await input.click();
  await input.fill(MESSAGE);
  await input.press("Enter");
  await sleep(600);
  await page.screenshot({ path: `${OUT}repro-0-queued.png` });

  // Sample DOM + WS while the turn runs.
  for (let i = 1; i <= 10; i++) {
    await sleep(3000);
    const snap = await page.evaluate(() => ({
      user: document.querySelectorAll('[data-testid="user-message-row"]').length,
      assistant: document.querySelectorAll('[data-testid="assistant-message-row"]').length,
      cursor: document.querySelectorAll(".streaming-cursor").length,
      stop: !!document.querySelector('[data-testid="chat-composer-stop"]'),
      lastText: (
        document.querySelector('[data-testid="assistant-message-row"]')?.textContent ?? ""
      ).slice(-80),
    }));
    console.log(`t+${i * 3}s: ${JSON.stringify(snap)}`);
    if (!snap.stop && i > 2) break; // turn finished
  }
  await page.screenshot({ path: `${OUT}repro-1-after.png` });

  const after = await page.evaluate(async (sid) => {
    const d = await fetch(`/api/sessions/${sid}`).then((r) => r.json());
    return d.messages.map((m) => `${m.role}:${m.content.slice(0, 40)}`);
  }, SESSION_ID).catch(() => []);
  console.log("backend messages after:", JSON.stringify(after, null, 1));
  console.log(`POSTs to /messages: ${posts.length}`);
  for (const p of posts) console.log(`  POST ${p.body}`);

  await browser.close();
  console.log(`REPRO_OK (console_errors=${errors.length})`);
  if (errors.length) console.log(errors.slice(0, 6).join("\n"));
} finally {
  killServer();
  await sleep(500);
}
