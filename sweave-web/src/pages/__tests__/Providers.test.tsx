/**
 * Settings providers tab: universe × availability + keychain actions.
 *
 * Pins: provider cards render connected/via/suffix states, local
 * providers get no key form, pending imports show the adopt banner,
 * and save/import/sync call the matching client methods + refresh
 * the ["providers"] query.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProvidersSettings } from "../Settings";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: {
    listProviders: vi.fn(),
    setCredential: vi.fn(),
    deleteCredential: vi.fn(),
    importCredentials: vi.fn(),
    syncCredentials: vi.fn(),
    resolveCredential: vi.fn(),
  },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({ pushNotification }),
}));

const listProviders = vi.mocked(api.listProviders);
const setCredential = vi.mocked(api.setCredential);
const importCredentials = vi.mocked(api.importCredentials);
const syncCredentials = vi.mocked(api.syncCredentials);
const resolveCredential = vi.mocked(api.resolveCredential);

const DATA = {
  providers: [
    { id: "zai", models: ["a", "b"], connected: true, via: "sweave", key_suffix: "1234", local: false },
    { id: "openrouter", models: ["c"], connected: false, via: null, key_suffix: null, local: false },
    { id: "ollama", models: ["d"], connected: null, via: null, key_suffix: null, local: true },
  ],
  pending_imports: [
    {
      provider: "openrouter",
      source: "isolated",
      reason: "rotated in opencode",
      sources: ["/x/opencode-data/opencode/auth.json", "/y/.local/share/opencode/auth.json"],
    },
  ],
} as never;

beforeEach(() => {
  vi.clearAllMocks();
  listProviders.mockResolvedValue(DATA);
});

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    ...render(
      <QueryClientProvider client={client}>
        <ProvidersSettings />
      </QueryClientProvider>,
    ),
    client,
  };
}

describe("Settings providers tab", () => {
  it("renders availability states and hides the key form for local providers", async () => {
    renderSection();
    expect((await screen.findByTestId("provider-status-zai")).textContent).toContain("connected");
    expect((await screen.findByTestId("provider-status-zai")).textContent).toContain("sweave");
    expect((await screen.findByTestId("provider-status-openrouter")).textContent).toContain("no key");
    expect((await screen.findByTestId("provider-status-ollama")).textContent).toContain("local");
    expect(screen.queryByTestId("provider-key-ollama")).toBeNull();
    expect(await screen.findByTestId("providers-import-all")).toBeTruthy();
  });

  it("saves a key and refreshes the query", async () => {
    setCredential.mockResolvedValue({ credential: {}, pushed: ["auth.json"] });
    const { client } = renderSection();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const input = (await screen.findByTestId("provider-key-openrouter")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-test" } });
    fireEvent.click(await screen.findByTestId("provider-save-openrouter"));
    await waitFor(() => {
      expect(setCredential).toHaveBeenCalledWith("openrouter", "sk-test");
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["providers"] });
      expect(pushNotification).toHaveBeenCalledWith("success", expect.stringContaining("openrouter"));
    });
  });

  it("adopts pending imports and syncs stores", async () => {
    importCredentials.mockResolvedValue({ adopted: ["openrouter"], skipped: [] });
    syncCredentials.mockResolvedValue({ adopted: [], pushed: {} });
    renderSection();
    fireEvent.click(await screen.findByTestId("providers-import-all"));
    await waitFor(() => {
      expect(importCredentials).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(await screen.findByTestId("providers-sync"));
    await waitFor(() => {
      expect(syncCredentials).toHaveBeenCalledTimes(1);
      expect(pushNotification).toHaveBeenCalledWith("success", expect.stringContaining("synced"));
    });
  });

  it("names the disagreeing stores and resolves keep-mine / take-theirs", async () => {
    resolveCredential.mockResolvedValue({
      provider: "openrouter",
      choice: "mine",
      key_suffix: "1111",
      pushed: ["a", "b"],
      adopted_from: null,
    });
    const { client } = renderSection();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const row = await screen.findByTestId("provider-pending-openrouter");
    expect(row.textContent).toContain("rotated in opencode");
    expect(row.textContent).toContain("isolated copy");
    expect(row.textContent).toContain("real store");
    fireEvent.click(await screen.findByTestId("provider-keep-openrouter"));
    await waitFor(() => {
      expect(resolveCredential).toHaveBeenCalledWith("openrouter", "mine");
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["providers"] });
      expect(pushNotification).toHaveBeenCalledWith(
        "success",
        expect.stringContaining("kept ours"),
      );
    });
  });

  it("says why when nothing was adoptable", async () => {
    importCredentials.mockResolvedValue({
      adopted: [],
      skipped: [{ provider: "github-copilot", reason: "oauth flow pending (detect-only)" }],
    });
    renderSection();
    fireEvent.click(await screen.findByTestId("providers-import-all"));
    await waitFor(() => {
      expect(pushNotification).toHaveBeenCalledWith(
        "warning",
        expect.stringContaining("oauth flow pending"),
      );
    });
  });
});
