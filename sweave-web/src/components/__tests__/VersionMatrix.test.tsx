/**
 * Versions card (per-part versioning ruling 2026-09-20).
 *
 * Pins: all five rows render from GET /api/version, null parts read
 * "unknown" (never blank), and a backend failure degrades to the
 * honest note instead of an empty card.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { VersionMatrix } from "@/components/VersionMatrix";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: { getVersions: vi.fn() },
}));

const getVersions = vi.mocked(api.getVersions);

const MATRIX = {
  backend: "0.1.1",
  engine: "0.2.0",
  web: "0.1.0",
  engine_protocol: "3",
  delegation_schema: 13,
};

beforeEach(() => {
  vi.clearAllMocks();
});

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <VersionMatrix />
    </QueryClientProvider>,
  );
}

describe("VersionMatrix", () => {
  it("renders every row from the version matrix", async () => {
    getVersions.mockResolvedValue(MATRIX);
    renderCard();
    expect((await screen.findByTestId("versions-row-backend")).textContent).toContain("0.1.1");
    expect((await screen.findByTestId("versions-row-engine")).textContent).toContain("0.2.0");
    expect((await screen.findByTestId("versions-row-web")).textContent).toContain("0.1.0");
    expect((await screen.findByTestId("versions-row-engine_protocol")).textContent).toContain("3");
    expect((await screen.findByTestId("versions-row-delegation_schema")).textContent).toContain("13");
  });

  it("reads unknown for unreadable parts", async () => {
    getVersions.mockResolvedValue({ ...MATRIX, engine: null });
    renderCard();
    expect((await screen.findByTestId("versions-row-engine")).textContent).toContain("unknown");
  });

  it("degrades honestly when the backend is unreachable", async () => {
    getVersions.mockRejectedValue(new Error("down"));
    renderCard();
    expect(await screen.findByTestId("versions-unavailable")).toBeTruthy();
  });
});
