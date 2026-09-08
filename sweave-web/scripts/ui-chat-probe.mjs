/**
 * Chat-surface visual probe (dev-only aid).
 *
 * Spawns the Vite dev server (proxying /api to the backend on :8100),
 * drives the system Edge headlessly to a REAL session's /chat surface,
 * and captures screenshots (thread with messages + composer states).
 *
 * Usage:  node scripts/ui-chat-probe.mjs [sessionId]
 * Requires the backend on 127.0.0.1:8100.
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE =
  process.env.SWEAVE_BROWSER ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 4175;
const BASE = `http://localhost:${PORT}`;
const SESSION_ID = process.argv[2] ?? "final_test-20260908-145152-812abb";
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

  // Expand + activate the session's project, then pick the session.
  const projName = SESSION_ID.replace(/-\d{8}-\d{6}-[0-9a-f]+$/, "");
  const projRow = page.locator('[data-testid="project-session-tree"] button', {
    hasText: projName,
  });
  if (await projRow.count()) {
    console.log(`project rows matched: ${await projRow.count()}`);
    await projRow.first().click();
    await sleep(1200);
    await page.screenshot({ path: `${OUT}debug-after-project-click.png` });
  } else {
    console.log(`WARN: project row for "${projName}" not found`);
  }
  const sessRow = page.locator(`[data-testid="session-tree-item-${SESSION_ID}"]`);
  if (await sessRow.count()) {
    await sessRow.first().click();
    await sleep(1500);
  } else {
    console.log(`WARN: session row ${SESSION_ID} not visible in tree`);
  }

  // Navigate to chat.
  await page.locator('[data-testid="nav-chat"]').click().catch(() => {});
  await sleep(2000);
  await page.screenshot({ path: `${OUT}chat-thread.png`, fullPage: true });

  const counts = await page.evaluate(async (sessionId) => {
    const apiCount = await fetch(`/api/sessions/${sessionId}`)
      .then((r) => r.json())
      .then((d) => d.messages.length)
      .catch(() => -1);
    return {
      apiMessages: apiCount,
      userRows: document.querySelectorAll('[data-testid="user-message-row"]').length,
      assistantRows: document.querySelectorAll('[data-testid="assistant-message-row"]').length,
      invalidDates: Array.from(document.querySelectorAll("time")).filter(
        (t) => t.textContent?.includes("Invalid"),
      ).length,
      renderedIds: Array.from(document.querySelectorAll("[data-message-id]")).map(
        (el) => el.getAttribute("data-message-id"),
      ),
      rowDetail: Array.from(document.querySelectorAll("[data-message-id]")).map((el) => ({
        id: el.getAttribute("data-message-id"),
        userRow: !!el.querySelector('[data-testid="user-message-row"]'),
        assistantRow: !!el.querySelector('[data-testid="assistant-message-row"]'),
        cls: (el.className || "").slice(0, 60),
      })),
      topbar: (() => {
        const header = document.querySelector("header");
        if (!header) return null;
        const kids = Array.from(header.querySelectorAll(":scope > div")).map((d) => {
          const r = d.getBoundingClientRect();
          return { x: Math.round(r.x), w: Math.round(r.width), sw: d.scrollWidth, cw: d.clientWidth };
        });
        const hr = header.getBoundingClientRect();
        return {
          header: { x: Math.round(hr.x), w: Math.round(hr.width), sw: header.scrollWidth, cw: header.clientWidth },
          kids,
          docScrollW: document.documentElement.scrollWidth,
          innerW: window.innerWidth,
        };
      })(),
    };
  }, SESSION_ID).catch(() => null);
  console.log("counts:", JSON.stringify(counts));

  await browser.close();
  console.log(`PROBE_OK -> ${OUT} (console_errors=${errors.length})`);
  if (errors.length) console.log(errors.slice(0, 8).join("\n"));
} finally {
  killServer();
  await sleep(500);
}
