/**
 * R4.1 e2e: foundation nav + scaffolds.
 *
 * The wave-1 shell.spec.ts is the smoke test (renders +
 * theme-switch). This file is R4.1's e2e: the project switcher,
 * session tree, scaffold presence, and the custom-color
 * picker. The tests assume the backend on :8100 is up (the
 * playwright.config.ts has reuseExistingServer=true; the
 * test_routers_smoke + test_r4_1_ws_events suites prove the
 * server works end-to-end).
 *
 * State setup: a small helper (``setupActiveProject``)
 * activates a project with one session via the API. The
 * state is shared across tests in the same run (the backend
 * persists; we activate what we need at the top of each
 * test).
 *
 * Local-run caveat (R4.1 step 4): Playwright 1.63.0 needs
 * chromium build 1243; the machines in this repo's history
 * carry builds 1228/1234. The wave-1 e2e docstring covers
 * this -- the e2e suite is a CI-time concern. The local
 * gates (pytest + run.py --check + vitest + npm run build)
 * are the green-light; the e2e specs are checked in so CI
 * can run them.
 */
import { test, expect, type APIRequestContext } from "@playwright/test";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

const BACKEND = "http://127.0.0.1:8100";
const SPA = "http://127.0.0.1:3000";

/** Create a project at a temp dir + activate it; return the name. */
async function setupActiveProject(
  request: APIRequestContext,
): Promise<string> {
  // Use a unique project name + temp dir per test run so
  // concurrent e2e runs don't collide. The project name is
  // also the URL slug the UI uses for nav testids.
  const stamp = Date.now().toString(36);
  const name = `e2e-${stamp}`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sweave-e2e-"));
  const create = await request.post(`${BACKEND}/api/projects`, {
    data: { name, path: dir, description: "R4.1 e2e scratch" },
  });
  if (!create.ok()) {
    throw new Error(`create project failed: ${create.status()} ${await create.text()}`);
  }
  const activate = await request.post(
    `${BACKEND}/api/projects/${encodeURIComponent(name)}/active`,
    { data: {} },
  );
  if (!activate.ok()) {
    throw new Error(`activate project failed: ${activate.status()}`);
  }
  return name;
}

/** Add a session to the active project. Returns the session id. */
async function createSession(
  request: APIRequestContext,
  projectName: string,
  sessionName: string,
): Promise<string> {
  const r = await request.post(`${BACKEND}/api/sessions`, {
    data: { name: sessionName, project_name: projectName },
  });
  if (!r.ok()) {
    throw new Error(`create session failed: ${r.status()} ${await r.text()}`);
  }
  const body = await r.json();
  return body.session.id as string;
}

test.describe("R4.1 foundation nav", () => {
  test("the sidebar shows project switcher + session tree + scaffolds", async ({
    page,
    request,
  }) => {
    const projectName = await setupActiveProject(request);
    await page.goto(`${SPA}/chat`);
    // Project switcher is mounted in the sidebar.
    await expect(page.getByTestId("project-switcher-toggle")).toBeVisible();
    // Session tree is mounted (no sessions yet, so the empty
    // hint is what shows).
    await expect(page.getByTestId("session-tree")).toBeVisible();
    // The four R4.1 nav entries (Funnels + Pane shells).
    await expect(page.getByTestId("nav-chat")).toBeVisible();
    await expect(page.getByTestId("nav-children")).toBeVisible();
    await expect(page.getByTestId("nav-memory")).toBeVisible();
    await expect(page.getByTestId("nav-agents")).toBeVisible();
    await expect(page.getByTestId("nav-settings")).toBeVisible();
  });

  test("switching projects swaps the session list", async ({
    page,
    request,
  }) => {
    // Create two projects with a session in each.
    const stamp = Date.now().toString(36);
    const a = await setupActiveProject(request);
    await createSession(request, a, `sess-${stamp}-a`);
    const b = await setupActiveProject(request);
    await createSession(request, b, `sess-${stamp}-b`);

    await page.goto(`${SPA}/chat`);
    // Start on the second project (b). The session tree shows b's session.
    await expect(page.getByTestId("project-switcher-toggle")).toContainText(b);
    await expect(page.getByText(`sess-${stamp}-b`)).toBeVisible();

    // Switch to a via the dropdown.
    await page.getByTestId("project-switcher-toggle").click();
    await page.getByTestId(`project-option-${a}`).click();

    // The tree refreshes (driven by the step-1b session.created
    // + project events + the invalidation map). We assert
    // a's session appears + b's session does not.
    await expect(page.getByTestId("project-switcher-toggle")).toContainText(a);
    await expect(page.getByText(`sess-${stamp}-a`)).toBeVisible();
    await expect(page.getByText(`sess-${stamp}-b`)).toHaveCount(0);
  });

  test("the inline create-session form adds a session to the tree", async ({
    page,
    request,
  }) => {
    const projectName = await setupActiveProject(request);
    await page.goto(`${SPA}/chat`);

    // The form is at the bottom of the session tree.
    await page.getByTestId("session-tree-name-input").fill("e2e-new-session");
    await page.getByTestId("session-tree-create-btn").click();

    // The new session appears in the tree (driven by the
    // step-1b session.created WS event + the invalidation map).
    await expect(
      page.getByTestId("session-tree").getByText("e2e-new-session"),
    ).toBeVisible({ timeout: 5000 });
  });
});

test.describe("R4.1 scaffold presence", () => {
  test("every R4.1 surface renders the scaffold + Pending R4.X badge", async ({
    page,
  }) => {
    const cases = [
      { url: "/memory", surface: "Memory", milestone: "R4.4" },
      { url: "/agents", surface: "Agents", milestone: "R4.4" },
      { url: "/settings", surface: "Settings", milestone: "R4.4" },
    ];
    for (const { url, surface, milestone } of cases) {
      await page.goto(`${SPA}${url}`);
      const slug = surface.toLowerCase();
      await expect(page.getByTestId(`scaffold-${slug}`)).toBeVisible();
      await expect(page.getByTestId(`scaffold-${slug}-title`)).toHaveText(surface);
      await expect(page.getByTestId(`scaffold-${slug}-badge`)).toHaveText(
        `Pending ${milestone}`,
      );
    }
  });

  test("delegation detail scaffold shows the route id", async ({ page }) => {
    await page.goto(`${SPA}/delegations/abc-123`);
    await expect(page.getByTestId("page-delegation-detail")).toBeVisible();
    await expect(page.getByTestId("page-delegation-detail-id")).toHaveText(
      "abc-123",
    );
    await expect(page.getByTestId("scaffold-delegation-badge")).toHaveText(
      "Pending R4.3",
    );
  });
});

test.describe("R4.1 theme + custom-color", () => {
  test("switching presets + custom-color override both update the data-theme + CSS variables", async ({
    page,
  }) => {
    await page.goto(`${SPA}/chat`);

    // Switch preset to nord.
    await page.getByTestId("theme-switcher-toggle").click();
    await page.getByTestId("theme-option-nord").click();
    let theme = await page.evaluate(() =>
      document.documentElement.getAttribute("data-theme"),
    );
    expect(theme).toBe("nord");

    // Open the custom-color editor (lives inside the switcher
    // dropdown; open it again + change a token).
    await page.getByTestId("theme-switcher-toggle").click();
    // Set background to a known color.
    const colorInput = page.getByTestId("custom-color-background");
    await colorInput.evaluate(
      (el, value) => {
        (el as HTMLInputElement).value = value;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      },
      "#ff00ff",
    );

    // The runtime injects rgb(255 0 255) into the document's
    // <style id="sweave-theme-vars">. The override path runs
    // through the same writer; verify the document sees it.
    const overrideText = await page.evaluate(() => {
      const el = document.getElementById("sweave-theme-vars");
      return el ? (el as HTMLStyleElement).textContent : null;
    });
    expect(overrideText).toBeTruthy();
    expect(overrideText).toContain("--color-background: rgb(255 0 255)");
  });
});

// Suppress an unused-import warning for the runtime helpers
// (fs / os / path) used by the project-state helpers above.
void fs;
void os;
void path;
