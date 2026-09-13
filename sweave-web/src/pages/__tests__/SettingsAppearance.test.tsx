/**
 * Settings appearance tests (2026-09-13 cleanup).
 *
 * Appearance (theme + font size) is global, so it renders with NO active
 * project. The per-token Customize editor stays hidden behind its toggle
 * until opened (29 presets + 37 pickers would otherwise be a wall).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsPage } from "../Settings";
import { ThemeProvider } from "@/lib/theme/ThemeProvider";

vi.mock("@/api/client", () => ({
  api: {
    getModels: vi.fn().mockResolvedValue({ providers: {} }),
    setDefaultModel: vi.fn(),
    regenerateModels: vi.fn(),
    listHarnesses: vi.fn().mockResolvedValue([]),
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

describe("Settings without an active project", () => {
  it("still renders theme presets and the font-size control", async () => {
    renderPage();
    expect(await screen.findByTestId("settings-preset-carbon")).toBeTruthy();
    expect(await screen.findByTestId("settings-font-scale")).toBeTruthy();
    // Project-gated surfaces stay out.
    expect(screen.queryByTestId("models-sync")).toBeNull();
  });

  it("keeps the per-token editor hidden until its toggle opens", async () => {
    renderPage();
    await screen.findByTestId("settings-customize-toggle");
    expect(screen.queryByTestId("custom-color-editor")).toBeNull();
    fireEvent.click(screen.getByTestId("settings-customize-toggle"));
    expect(await screen.findByTestId("custom-color-editor")).toBeTruthy();
  });
});
