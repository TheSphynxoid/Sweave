/**
 * Settings models tab: Sync models button + global-vs-project honesty.
 *
 * Pins: the button calls POST /models/regenerate (long budget),
 * invalidates the ["models"] query on success with an added/removed
 * summary notification, and surfaces failures without touching the
 * registry display. The default-model card names the two-file
 * layering (global default, per-project override).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ModelsSettings } from "../Settings";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: {
    getModels: vi.fn(),
    setDefaultModel: vi.fn(),
    regenerateModels: vi.fn(),
    listHarnesses: vi.fn().mockResolvedValue([]),
  },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({ pushNotification }),
}));

const regenerate = vi.mocked(api.regenerateModels);

const MODELS = {
  default: "opencode/some-model",
  providers: { opencode: ["some-model", "other-model"] },
} as never;

beforeEach(() => {
  vi.clearAllMocks();
});

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    ...render(
      <QueryClientProvider client={client}>
        <ModelsSettings models={MODELS} />
      </QueryClientProvider>,
    ),
    client,
  };
}

describe("Settings models sync", () => {
  it("names the global default with its per-project override path", () => {
    renderSection();
    expect(
      screen.getByText(/Global default for the orchestrator/)
    ).toBeTruthy();
    expect(screen.getByText(/\.sweave\/config\.yaml/)).toBeTruthy();
  });
  it("syncs and notifies with the added/removed summary", async () => {
    regenerate.mockResolvedValue({
      success: true,
      providers: 10,
      models: 100,
      added: 3,
      removed: 1,
      source: "models.dev+serve",
      path: "models.yaml",
    });
    const { client } = renderSection();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    fireEvent.click(await screen.findByTestId("models-sync"));
    await waitFor(() => {
      expect(regenerate).toHaveBeenCalledTimes(1);
      expect(pushNotification).toHaveBeenCalledWith(
        "success",
        expect.stringContaining("+3/−1"),
      );
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["models"] });
    });
  });

  it("disables the button while syncing and reports failures", async () => {
    let release!: () => void;
    regenerate.mockReturnValue(
      new Promise((res) => {
        release = () =>
          res({
            success: true,
            providers: 1,
            models: 1,
            added: 0,
            removed: 0,
            source: "x",
            path: "y",
          });
      }),
    );
    renderSection();
    const btn = (await screen.findByTestId("models-sync")) as HTMLButtonElement;
    fireEvent.click(btn);
    await waitFor(() => {
      expect(btn.disabled).toBe(true);
    });
    expect(screen.getByText("Syncing…")).toBeTruthy();
    release();
    await waitFor(() => {
      expect(btn.disabled).toBe(false);
    });

    regenerate.mockRejectedValueOnce(new Error("boom"));
    fireEvent.click(btn);
    await waitFor(() => {
      expect(pushNotification).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("boom"),
      );
    });
  });
});
