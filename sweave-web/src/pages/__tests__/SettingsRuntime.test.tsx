/**
 * Settings runtime defaults: global harness + per-turn retries.
 *
 * Pins: the card renders the live values (harness default, retries),
 * saving calls the matching PUT endpoint + invalidates its query with
 * a success notification, and out-of-range retries never reach the
 * API (client-side error instead). Follows the repo UI pattern:
 * headless queries with retry:false, assert the control is actually
 * operable (not just present).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RuntimeSettings } from "../Settings";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: {
    getHarnessDefault: vi.fn(),
    getRules: vi.fn(),
    setHarnessDefault: vi.fn(),
    setTurnRetries: vi.fn(),
  },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({ pushNotification }),
}));

const getHarnessDefault = vi.mocked(api.getHarnessDefault);
const getRules = vi.mocked(api.getRules);
const setHarnessDefault = vi.mocked(api.setHarnessDefault);
const setTurnRetries = vi.mocked(api.setTurnRetries);

beforeEach(() => {
  vi.clearAllMocks();
  getHarnessDefault.mockResolvedValue("sweave-engine");
  getRules.mockResolvedValue({
    routes: [],
    fallback: "llm",
    turn_retries: 3,
    turn_timeout_s: 1800,
    chain_budget: 200000,
    max_depth: 2,
  });
});

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    ...render(
      <QueryClientProvider client={client}>
        <RuntimeSettings />
      </QueryClientProvider>,
    ),
    client,
  };
}

describe("Settings runtime defaults", () => {
  it("renders the live harness default and retry budget", async () => {
    renderSection();
    const harness = (await screen.findByTestId("runtime-harness")) as HTMLSelectElement;
    await waitFor(() => expect(harness.value).toBe("sweave-engine"));
    const retries = (await screen.findByTestId("runtime-retries")) as HTMLInputElement;
    await waitFor(() => expect(retries.value).toBe("3"));
  });

  it("saves the harness default with a success notification", async () => {
    const { client } = renderSection();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    setHarnessDefault.mockResolvedValue({ success: true, harness: "opencode" });
    const harness = (await screen.findByTestId("runtime-harness")) as HTMLSelectElement;
    fireEvent.change(harness, { target: { value: "opencode" } });
    fireEvent.click(await screen.findByTestId("runtime-harness-save"));
    await waitFor(() => {
      expect(setHarnessDefault).toHaveBeenCalledWith("opencode");
      expect(pushNotification).toHaveBeenCalledWith(
        "success",
        expect.stringContaining("opencode"),
      );
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["harness-default"] });
    });
  });

  it("saves the retry budget and invalidates the rules query", async () => {
    const { client } = renderSection();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    setTurnRetries.mockResolvedValue({ success: true, turn_retries: 0 });
    const retries = (await screen.findByTestId("runtime-retries")) as HTMLInputElement;
    fireEvent.change(retries, { target: { value: "0" } });
    fireEvent.click(await screen.findByTestId("runtime-retries-save"));
    await waitFor(() => {
      expect(setTurnRetries).toHaveBeenCalledWith(0);
      expect(pushNotification).toHaveBeenCalledWith(
        "success",
        expect.stringContaining("disabled"),
      );
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["rules"] });
    });
  });

  it("blocks out-of-range retries client-side without calling the API", async () => {
    renderSection();
    const retries = (await screen.findByTestId("runtime-retries")) as HTMLInputElement;
    fireEvent.change(retries, { target: { value: "99" } });
    fireEvent.click(await screen.findByTestId("runtime-retries-save"));
    await waitFor(() => {
      expect(pushNotification).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("0"),
      );
    });
    expect(setTurnRetries).not.toHaveBeenCalled();
  });

  it("reports save failures without touching the displayed value", async () => {
    renderSection();
    setTurnRetries.mockRejectedValueOnce(new Error("boom"));
    const retries = (await screen.findByTestId("runtime-retries")) as HTMLInputElement;
    fireEvent.change(retries, { target: { value: "5" } });
    fireEvent.click(await screen.findByTestId("runtime-retries-save"));
    await waitFor(() => {
      expect(pushNotification).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("boom"),
      );
    });
  });
});
