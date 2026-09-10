import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 4771;
const BASE = `http://localhost:${PORT}`;

const server = spawn("npx", ["vite", "--port", String(PORT), "--strictPort"], {
  stdio: "ignore", shell: true, cwd: process.cwd(),
});
function killServer() {
  if (!server.pid) return;
  try {
    spawn("taskkill", ["/PID", String(server.pid), "/T", "/F"], { stdio: "ignore", shell: true });
  } catch {}
}
process.on("exit", killServer);

async function waitUp() {
  for (let i = 0; i < 300; i++) {
    try { const r = await fetch(BASE); if (r.ok) return; } catch {}
    await sleep(500);
  }
  throw new Error("dev server did not start");
}

try {
  await waitUp();
  const browser = await chromium.launch({ executablePath: EDGE, headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  await page.goto(`${BASE}/`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
  await sleep(2500);

  const out = await page.evaluate(() => {
    const el = document.createElement("div");
    el.className = "px-3";
    document.body.appendChild(el);

    // Style-tag text ground truth.
    const tags = Array.from(document.querySelectorAll("style")).map((s, i) => ({
      i,
      len: s.textContent?.length ?? 0,
      hasPx3: (s.textContent ?? "").includes(".px-3"),
    }));

    // FIXED walker: recurse only into non-empty containers.
    const matches = [];
    const walk = (rules) => {
      for (const r of rules) {
        const nested = r.cssRules && r.cssRules.length ? r.cssRules : null;
        if (nested) { walk(nested); continue; }
        if (r.selectorText) {
          try {
            if (el.matches(r.selectorText)) {
              matches.push({
                sel: r.selectorText.slice(0, 60),
                css: r.style?.cssText.slice(0, 120) ?? "",
              });
            }
          } catch {
            /* complex selector — skip */
          }
        }
      }
    };
    for (const s of document.styleSheets) {
      try { walk(s.cssRules); } catch {}
    }

    el.remove();
    return {
      styleTags: tags,
      px3Computed: getComputedStyle(document.createElement("div")).padding, // sanity (no class)
      matches,
    };
  });

  console.log(JSON.stringify(out, null, 2));
  await browser.close();
} finally {
  killServer();
  await sleep(800);
}
