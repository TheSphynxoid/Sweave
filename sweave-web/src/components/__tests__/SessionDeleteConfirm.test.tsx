/**
 * Session delete confirmation tests (R6 follow-up).
 *
 * Clicking a session's delete button must NOT delete immediately: a
 * confirmation dialog naming the session appears, and `api.deleteSession`
 * is only called after the user confirms. Cancel / Escape must not delete.
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionTree } from "@/components/SessionTree";
import { ProjectSessionTree } from "@/components/ProjectSessionTree";
import { api } from "@/api/client";
import type { SessionSummary, ProjectSummary } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    listProjects: vi.fn(),
    listSessions: vi.fn(),
    deleteSession: vi.fn(),
    deleteProject: vi.fn(),
    createSession: vi.fn(),
  },
}));

const pushNotification = vi.fn();
const setActiveSession = vi.fn().mockResolvedValue(undefined);
const setActiveProject = vi.fn().mockResolvedValue(undefined);

vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({
    activeProject: { name: "demo" },
    activeSession: { id: "s-1" },
    setActiveProject,
    setActiveSession,
    pushNotification,
  }),
}));

vi.mock("@/store/ui", () => ({
  useUIStore: () => vi.fn(),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

const listSessionsMock = vi.mocked(api.listSessions);
const deleteSessionMock = vi.mocked(api.deleteSession);
const listProjectsMock = vi.mocked(api.listProjects);

function session(id: string, name: string): SessionSummary {
  return {
    id,
    name,
    project_name: "demo",
    created_at: "2026-09-10T00:00:00Z",
    updated_at: "2026-09-10T00:00:00Z",
    orchestrator_session_id: null,
    status: "active",
    message_count: 0,
    child_count: 0,
    memory_bank: "",
    active: id === "s-1",
  };
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeAll(() => {
  // jsdom has no scrollIntoView (ProjectSessionTree calls it on mount).
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  vi.clearAllMocks();
  listSessionsMock.mockResolvedValue([
    session("s-1", "alpha"),
    session("s-2", "beta"),
  ]);
  listProjectsMock.mockResolvedValue([
    {
      name: "demo",
      path: "C:/tmp/demo",
      description: "",
      created_at: "2026-09-10T00:00:00Z",
      updated_at: "2026-09-10T00:00:00Z",
      default_harness: "opencode",
      memory_bank: "",
      model_overrides: {},
      routing_rules: [],
      worktree_base: null,
      active: true,
    } as ProjectSummary,
  ]);
  deleteSessionMock.mockResolvedValue({ success: true });
});

describe("SessionTree delete confirmation", () => {
  it("does not call deleteSession until confirmed", async () => {
    renderWithClient(<SessionTree />);
    const row = await screen.findByLabelText("Delete beta");
    fireEvent.click(row);
    // Dialog appears, naming the session, and delete has NOT been called yet.
    expect(await screen.findByText(/Delete "beta"\? This cannot be undone\./i)).toBeTruthy();
    expect(deleteSessionMock).not.toHaveBeenCalled();
    // Confirm.
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(deleteSessionMock).toHaveBeenCalledWith("s-2"));
  });

  it("cancel does not delete", async () => {
    renderWithClient(<SessionTree />);
    fireEvent.click(await screen.findByLabelText("Delete alpha"));
    fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
    expect(deleteSessionMock).not.toHaveBeenCalled();
  });

  it("escape closes the dialog without deleting", async () => {
    renderWithClient(<SessionTree />);
    fireEvent.click(await screen.findByLabelText("Delete beta"));
    fireEvent.keyDown(screen.getByText(/Delete "beta"\?/), {
      key: "Escape",
      code: "Escape",
      bubbles: true,
    });
    await waitFor(() =>
      expect(screen.queryByText(/Delete "beta"\?/)).toBeNull(),
    );
    expect(deleteSessionMock).not.toHaveBeenCalled();
  });
});

describe("ProjectSessionTree delete confirmation", () => {
  it("does not call deleteSession until confirmed", async () => {
    renderWithClient(<ProjectSessionTree />);
    // The active project auto-expands, revealing its sessions directly.
    const row = await screen.findByLabelText("Delete beta");
    fireEvent.click(row);
    expect(await screen.findByText(/Delete "beta"\? This cannot be undone\./i)).toBeTruthy();
    expect(deleteSessionMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(deleteSessionMock).toHaveBeenCalledWith("s-2"));
  });

  it("cancel does not delete", async () => {
    renderWithClient(<ProjectSessionTree />);
    fireEvent.click(await screen.findByLabelText("Delete alpha"));
    fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
    expect(deleteSessionMock).not.toHaveBeenCalled();
  });
});
