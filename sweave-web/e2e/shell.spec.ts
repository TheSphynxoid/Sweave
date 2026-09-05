/**
 * E2E smoke test (M1.9 step 4).
 *
 * The R4 plan's wave-1 Playwright suite replaces the v1
 * test_full.py + test_sidebar_nav.js. This file is the
 * starting point: a single smoke test that boots a real
 * browser, navigates to the SPA, and asserts the shell
 * renders.
 *
 * The browser binary is whatever Playwright can find locally
 * (``chromium_headless_shell-*`` under ``%LOCALAPPDATA%``). If
 * no browser is available, the test is skipped (the wave-1
 * gates run on `tsc --noEmit` + `npm run build` + `npm test`
 * for the unit tests; e2e is a CI-time concern).
 */
import { test, expect } from "@playwright/test";

test.describe("sweave-web wave 1", () => {
  test("SPA shell renders", async ({ page }) => {
    await page.goto("/");
    // The shell renders the sidebar (data-testid) within a
    // reasonable timeout. The chat + children routes are
    // reachable; the new shell replaces the v1 vanilla UI.
    await expect(page.getByTestId("sidebar")).toBeVisible({
      timeout: 5000,
    });
    // Default route is /chat (the new App.tsx redirects / ->
    // /chat).
    await expect(page).toHaveURL(/\/chat$/);
  });

  test("theme switcher changes the data-theme attribute", async ({
    page,
  }) => {
    await page.goto("/chat");
    // Open the switcher + pick nord.
    await page.getByTestId("theme-switcher-toggle").click();
    await page.getByTestId("theme-option-nord").click();
    // The data-theme attribute is set on :root by the applier.
    const theme = await page.evaluate(() =>
      document.documentElement.getAttribute("data-theme"),
    );
    expect(theme).toBe("nord");
  });
});
