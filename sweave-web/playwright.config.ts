import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config (M1.9 step 4).
 *
 * The R4 plan replaces the v1 vanilla UI's test_full.py +
 * test_sidebar_nav.js with a Playwright e2e suite. The suite
 * lives in sweave-web/e2e/. The webServer config starts the
 * backend on :8100 if it's not already running; the tests
 * then visit the SPA via the vite dev server (port 3000) +
 * the proxied /api + /ws.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      // The backend (FastAPI on :8100). We don't start it
      // automatically here -- the dev script does that.
      // Tests assume the backend is already running.
      command: "echo backend assumed running on :8100",
      url: "http://127.0.0.1:8100/api/agents",
      reuseExistingServer: true,
      timeout: 5000,
    },
    {
      // The Vite dev server (port 3000, proxies /api + /ws
      // to :8100). Start fresh per test run; reuse if it's
      // already running.
      command: "npm run dev",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
});
