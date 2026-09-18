/**
 * Settings without an active project (global-settings contract).
 *
 * Appearance (theme + font size) is global, so it renders with NO active
 * project. So do the other global surfaces — Models, Providers, and
 * System (runtime defaults) — since none of them is per-project data.
 * Only the Project tab keeps an empty state until a project is active.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsPage } from "../Settings";
import { ThemeProvider } from "@/lib/theme/ThemeProvider";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: {
    getModels: vi.fn().mockResolvedValue({ providers: {} }),
    setDefaultModel: vi.fn(),
    regenerateModels: vi.fn(),
    listHarnesses: vi.fn().mockResolvedValue([]),
    getHarnessDefault: vi.fn().mockResolvedValue("opencode"),
    getRules: vi.fn().mockResolvedValue({
      routes: [],
      fallback: "llm",
      turn_retries: 3,
      turn_timeout_s: 1800,
      chain_budget: 200000,
      max_depth: 2,
    }),
    setHarnessDefault: vi.fn(),
    setTurnRetries: vi.fn(),
  },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({
    activeProject: null,
    activeSession: null,
    pushNotification,
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <SettingsPage />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function activateTab(name: string): void {
  // Radix Tabs activate on focus (automatic mode); fireEvent.click alone
  // never focuses in jsdom, so focus first, then click.
  const tabs = screen.getAllByRole("tab");
  const found = tabs.find((t) => t.textContent?.includes(name));
  if (!found) throw new Error(`tab ${name} not found`);
  found.focus();
  fireEvent.click(found);
}

describe("Settings without an active project", () => {
  it("still renders theme presets and the font-size control", async () => {
    renderPage();
    expect(await screen.findByTestId("settings-preset-carbon")).toBeTruthy();
    expect(await screen.findByTestId("settings-font-scale")).toBeTruthy();
  });

  it("keeps the per-token editor hidden until its toggle opens", async () => {
    renderPage();
    await screen.findByTestId("settings-customize-toggle");
    expect(screen.queryByTestId("custom-color-editor")).toBeNull();
    fireEvent.click(screen.getByTestId("settings-customize-toggle"));
    expect(await screen.findByTestId("custom-color-editor")).toBeTruthy();
  });

  it("renders the global Models tab with no project selected", async () => {
    renderPage();
    activateTab("Models");
    expect(await screen.findByTestId("models-sync")).toBeTruthy();
  });

  it("renders the global runtime defaults with no project selected", async () => {
    renderPage();
    activateTab("System");
    const harness = (await screen.findByTestId(
      "runtime-harness",
    )) as HTMLSelectElement;
    await waitFor(() => expect(harness.value).toBe("opencode"));
    expect(await screen.findByTestId("runtime-retries")).toBeTruthy();
    expect(vi.mocked(api.getHarnessDefault)).toHaveBeenCalled();
  });

  it("shows an empty state on the Project tab with no project selected", async () => {
    renderPage();
    activateTab("Project");
    expect(await screen.findByText(/No active project/)).toBeTruthy();
  });

  it("offers the chat backdrop switch and persists the pick", async () => {
    renderPage();
    const group = await screen.findByTestId("settings-backdrop");
    expect(group).toBeTruthy();
    fireEvent.click(screen.getByTestId("settings-backdrop-floral"));
    expect(window.localStorage.getItem("sweave.theme.backdrop")).toBe("floral");
    expect(document.documentElement.getAttribute("data-chat-backdrop")).toBe("floral");
    fireEvent.click(screen.getByTestId("settings-backdrop-glow"));
    expect(window.localStorage.getItem("sweave.theme.backdrop")).toBe("glow");
  });
});
