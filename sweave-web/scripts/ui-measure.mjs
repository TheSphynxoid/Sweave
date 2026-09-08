/**
 * UI layout measurement harness — same dev-server/browser setup as
 * ui-shot.mjs, but prints objective DOM numbers (boxes, computed padding,
 * gaps, matched CSS rules) instead of pixels. Use it to verify
 * padding/margin fixes on the app shell AND on the chat-lab gallery.
 *
 * Usage:  npm run ui:measure
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE =
  process.env.SWEAVE_BROWSER ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 4173;
const BASE = `http://localhost:${PORT}`;

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
  await page.goto(`${BASE}/`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2000);

  const shell = await page.evaluate(() => {
    const px = (el, prop) => (el ? parseFloat(getComputedStyle(el)[prop]) : null);
    const rect = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const findRule = (needle) => {
      for (const sheet of document.styleSheets) {
        try {
          for (const r of sheet.cssRules) {
            if (r.selectorText?.includes(needle) && r.style?.cssText) {
              return r.cssText.slice(0, 140);
            }
          }
        } catch {
          /* cross-origin sheet */
        }
      }
      return null;
    };

    const chatNav = document.querySelector('[data-testid="nav-chat"]');
    const childrenNav = document.querySelector('[data-testid="nav-children"]');
    let navGap = null;
    if (chatNav && childrenNav) {
      const a = chatNav.getBoundingClientRect();
      const b = childrenNav.getBoundingClientRect();
      navGap = Math.round(b.y - (a.y + a.height));
    }

    return {
      sidebar: rect('[data-testid="sidebar"]'),
      navItem: rect('[data-testid="nav-chat"]'),
      navPadLeft: px(chatNav, "paddingLeft"),
      navPadLeftRule: findRule(".pl-4"),
      navGap,
      chatPage: rect('[data-testid="chat-page"]'),
      topbarButtons: Array.from(document.querySelectorAll("header button")).map((b) => ({
        testid: b.getAttribute("data-testid"),
        h: Math.round(b.getBoundingClientRect().height),
      })),
    };
  });

  // Chat lab (fixture gallery — no backend needed).
  await page.goto(`${BASE}/dev/chat-lab`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2000);

  const lab = await page.evaluate(() => {
    const px = (el, prop) => (el ? parseFloat(getComputedStyle(el)[prop]) : null);
    const rect = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const container = document.querySelector('[data-testid="chat-lab"]');
    const composer = document.querySelector('[data-testid="chat-lab-composer-input"]');
    const send = document.querySelector('[data-testid="chat-lab-send"]');
    const assistant = document.querySelector('[data-testid="chat-lab-bubble-assistant"]');
    const user = document.querySelector('[data-testid="chat-lab-bubble-user"]');

    const labRect = container?.getBoundingClientRect();
    const userRect = user?.getBoundingClientRect();
    return {
      labContainer: {
        ...rect('[data-testid="chat-lab"]'),
        padLeft: px(container, "paddingLeft"),
        padRight: px(container, "paddingRight"),
      },
      composer: {
        ...rect('[data-testid="chat-lab-composer-input"]'),
        padLeft: px(composer, "paddingLeft"),
        minHeight: px(composer, "minHeight"),
      },
      sendButton: rect('[data-testid="chat-lab-send"]'),
      bubbleAssistant: {
        ...rect('[data-testid="chat-lab-bubble-assistant"]'),
        padLeft: px(assistant, "paddingLeft"),
      },
      bubbleUser: {
        ...rect('[data-testid="chat-lab-bubble-user"]'),
        // distance from the lab container's right edge (ml-auto alignment)
        rightInset:
          labRect && userRect ? Math.round(labRect.right - userRect.right) : null,
      },
    };
  });

  console.log(JSON.stringify({ shell, lab }, null, 2));
  await browser.close();
} finally {
  killServer();
  await sleep(500);
}
