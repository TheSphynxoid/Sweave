import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

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

const count = (text, pat) => text.split(pat).length - 1;

try {
  await waitUp();

  // Variant A: raw transformed CSS (?direct)
  const direct = await (await fetch(`${BASE}/src/styles/globals.css?direct`)).text();
  writeFileSync("dev-css-direct.txt", direct);

  // Variant B: the JS module the page actually executes (CSS embedded as a string)
  const mod = await (await fetch(`${BASE}/src/styles/globals.css`)).text();
  writeFileSync("dev-css-module.js", mod);

  console.log("direct bytes:", direct.length, "| module bytes:", mod.length);
  for (const pat of [".px-3", ".pl-4", ".h-9", ".gap-3", "padding-inline"]) {
    console.log(`${pat}  direct=${count(direct, pat)}  module=${count(mod, pat)}`);
  }
} finally {
  killServer();
  await sleep(800);
}
