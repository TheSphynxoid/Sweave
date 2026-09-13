/**
 * Agents page: per-scope card gating (2026-09-11 seed model overrides).
 *
 * Pins: the model picker is editable for seed cards (a seed override
 * is persistable via setSpecialistModel) but Edit/Delete stay hidden
 * for seeds; orchestrator cards keep everything locked; project cards
 * keep the full edit affordances.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentsPage } from "../Agents";
import { api } from "@/api/client";
import type { SpecialistSummary } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    listSpecialists: vi.fn(),
    listHarnesses: vi.fn().mockResolvedValue([]),
    getModels: vi.fn().mockResolvedValue({ default: "opencode-go/glm-5.3-flash" }),
    setSpecialistModel: vi.fn(),
    deleteSpecialist: vi.fn(),
  },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({ pushNotification }),
}));

const listMock = vi.mocked(api.listSpecialists);

function spec(overrides?: Partial<SpecialistSummary>): SpecialistSummary {
  return {
    name: "backend-specialist",
    scope: "seed",
    is_orchestrator: false,
    role_ref: "backend",
    description: "Backend engineering",
    system_prompt: "You are a backend specialist",
    harness: "opencode",
    current_model: null,
    session_id: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockResolvedValue([
    spec(), // seed
    spec({
      name: "sql-expert",
      scope: "project",
      role_ref: null,
      description: "",
      system_prompt: "",
    }), // project
    spec({
      name: "orchestrator",
      scope: "project",
      is_orchestrator: true,
      role_ref: "orchestrator",
      description: "Supervisor",
      system_prompt: "You are the orchestrator",
    }), // orchestrator group
  ]);
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AgentsPage />
    </QueryClientProvider>,
  );
}

function waitForPicker(name: string, scope: string): Promise<HTMLElement> {
  return waitFor(() => {
    const el = document.querySelector(
      `[data-testid="model-picker-${scope}-${name}"]`,
    );
    expect(el).not.toBeNull();
    return el as HTMLElement;
  });
}

describe("AgentsPage seed model overrides", () => {
  it("enables the model picker on a seed card", async () => {
    renderPage();
    const seedPicker = await waitForPicker("backend-specialist", "seed");
    expect(seedPicker.hasAttribute("disabled")).toBe(false);
  });

  it("keeps the orchestrator model picker disabled", async () => {
    renderPage();
    const orch = await waitForPicker("orchestrator", "project");
    expect(orch.hasAttribute("disabled")).toBe(true);
  });

  it("shows Edit/Delete only on the project card (seed + orchestrator hide them)", async () => {
    renderPage();
    await waitForPicker("sql-expert", "project");
    expect(screen.getAllByText("Edit").length).toBe(1);
  });
});

describe("AgentsPage delete confirmation", () => {
  const deleteMock = vi.mocked(api.deleteSpecialist);

  it("asks first instead of deleting immediately", async () => {
    renderPage();
    await waitForPicker("sql-expert", "project");
    fireEvent.click(screen.getByText("Delete"));
    expect(deleteMock).not.toHaveBeenCalled();
    expect(await screen.findByTestId("confirm-delete-specialist-dialog")).toBeTruthy();
  });

  it("deletes on confirm and not on cancel", async () => {
    deleteMock.mockResolvedValue(undefined as never);
    renderPage();
    await waitForPicker("sql-expert", "project");
    fireEvent.click(screen.getByText("Delete"));
    fireEvent.click(await screen.findByTestId("confirm-delete-specialist-dialog-cancel"));
    expect(deleteMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Delete"));
    fireEvent.click(await screen.findByTestId("confirm-delete-specialist-dialog-confirm"));
    await waitFor(() => {
      expect(deleteMock).toHaveBeenCalledWith("sql-expert", "project");
    });
  });
});

describe("AgentsPage harness badge", () => {
  it("marks the native engine distinctly from opencode", async () => {
    listMock.mockResolvedValue([
      spec({ name: "native-one", harness: "sweave-engine" }),
      spec({ name: "legacy-one", harness: "opencode" }),
    ]);
    renderPage();
    const native = await screen.findByTestId("harness-badge-native-one");
    const legacy = await screen.findByTestId("harness-badge-legacy-one");
    expect(native.textContent).toBe("sweave-engine");
    expect(legacy.textContent).toBe("opencode");
    expect(native.className).not.toBe(legacy.className);
  });
});

describe("AgentsPage search filter", () => {
  it("narrows cards by name and restores on clear", async () => {
    renderPage();
    await waitForPicker("backend-specialist", "seed");
    fireEvent.change(screen.getByTestId("agents-search"), { target: { value: "sql" } });
    await waitFor(() => {
      expect(document.querySelector('[data-testid="model-picker-seed-backend-specialist"]')).toBeNull();
    });
    expect(
      document.querySelector('[data-testid="model-picker-project-sql-expert"]'),
    ).not.toBeNull();
    fireEvent.change(screen.getByTestId("agents-search"), { target: { value: "" } });
    await waitForPicker("backend-specialist", "seed");
  });

  it("shows an empty note when nothing matches", async () => {
    renderPage();
    await waitForPicker("backend-specialist", "seed");
    fireEvent.change(screen.getByTestId("agents-search"), { target: { value: "zzz-nope" } });
    expect(await screen.findByText(/No specialists match/)).toBeTruthy();
  });
});
