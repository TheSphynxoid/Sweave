/**
 * Models sync button (2026-09-20: the button 500'd with a bare code
 * when opencode was absent — axios's generic status text hid the
 * server's reason).
 *
 * Pins: success surfaces the report source, and failures surface the
 * server's `detail` (not "Request failed with status code 500");
 * errors without a detail fall back to the generic message.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ModelsSettings } from "../Settings";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: {
    regenerateModels: vi.fn(),
    setDefaultModel: vi.fn(),
  },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({ pushNotification }),
}));

const regenerateModels = vi.mocked(api.regenerateModels);

const MODELS = { providers: { p: ["m"] } } as never;

beforeEach(() => {
  vi.clearAllMocks();
});

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ModelsSettings models={MODELS} />
    </QueryClientProvider>,
  );
}

describe("Models sync button", () => {
  it("reports the sync source on success", async () => {
    regenerateModels.mockResolvedValue({
      success: true,
      providers: 2,
      models: 5,
      added: 1,
      removed: 0,
      source: "models.dev-only (no serve overlay: opencode not found)",
    } as never);
    renderSection();
    fireEvent.click(screen.getByTestId("models-sync"));
    await waitFor(() =>
      expect(pushNotification).toHaveBeenCalledWith(
        "success",
        expect.stringContaining("models.dev-only"),
      ),
    );
  });

  it("surfaces the server detail instead of the bare status code", async () => {
    const failure = Object.assign(new Error("Request failed with status code 500"), {
      response: { data: { detail: "models regenerate failed: refusing to write an empty registry" } },
    });
    regenerateModels.mockRejectedValue(failure);
    renderSection();
    fireEvent.click(screen.getByTestId("models-sync"));
    await waitFor(() =>
      expect(pushNotification).toHaveBeenCalledWith(
        "error",
        "Model sync failed: models regenerate failed: refusing to write an empty registry",
      ),
    );
  });

  it("falls back to the generic message without a detail", async () => {
    regenerateModels.mockRejectedValue(new Error("Network Error"));
    renderSection();
    fireEvent.click(screen.getByTestId("models-sync"));
    await waitFor(() =>
      expect(pushNotification).toHaveBeenCalledWith(
        "error",
        "Model sync failed: Network Error",
      ),
    );
  });
});
