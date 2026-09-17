/**
 * Sidebar consolidation tests (2026-09-13).
 *
 * The project/session tree is the sole session-switching surface:
 * - every project row shows its session count, even collapsed;
 * - the project holding the active session gets a marker;
 * - project delete opens a confirm dialog first (previously a single
 *   click deleted immediately); confirm deletes, cancel does not.
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProjectSessionTree } from "@/components/ProjectSessionTree";
import { api } from "@/api/client";
import type { SessionSummary } from "@/types";

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
    activeSession: { id: "s-1", name: "alpha", project_name: "demo" },
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
  useParams: () => ({}),
}));

const listSessionsMock = vi.mocked(api.listSessions);
const listProjectsMock = vi.mocked(api.listProjects);
const deleteProjectMock = vi.mocked(api.deleteProject);

function session(id: string, name: string, project: string): SessionSummary {
  return {
    id,
    name,
    project_name: project,
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

function project(name: string) {
  return {
    name,
    path: `C:/tmp/${name}`,
    description: "",
    created_at: "2026-09-10T00:00:00Z",
    updated_at: "2026-09-10T00:00:00Z",
    default_harness: "opencode",
    memory_bank: `project-${name}`,
    model_overrides: {},
    routing_rules: [],
    worktree_base: null,
    active: true,
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
  listProjectsMock.mockResolvedValue([project("demo"), project("other")]);
  listSessionsMock.mockImplementation(async (name?: string) =>
    name === "demo"
      ? [session("s-1", "alpha", "demo"), session("s-2", "beta", "demo")]
      : [session("s-9", "lonely", "other")],
  );
});

describe("project rows", () => {
  it("show a session count per project", async () => {
    renderWithClient(<ProjectSessionTree />);
    expect((await screen.findByTestId("project-session-count-demo")).textContent).toBe("2");
    expect((await screen.findByTestId("project-session-count-other")).textContent).toBe("1");
  });

  it("marks the project holding the active session (and no other)", async () => {
    renderWithClient(<ProjectSessionTree />);
    expect(await screen.findByTestId("project-active-dot-demo")).toBeTruthy();
    expect(screen.queryByTestId("project-active-dot-other")).toBeNull();
  });
});

describe("project delete confirmation", () => {
  it("opens a confirm dialog first instead of deleting immediately", async () => {
    renderWithClient(<ProjectSessionTree />);
    await screen.findByTestId("project-row-other");
    // Hover to reveal the delete button (opacity-0 until group-hover).
    fireEvent.mouseEnter(screen.getByTestId("project-row-other"));
    fireEvent.click(screen.getByRole("button", { name: "Delete other" }));
    expect(deleteProjectMock).not.toHaveBeenCalled();
    expect(await screen.findByTestId("confirm-delete-project-dialog")).toBeTruthy();
  });

  it("deletes on confirm and not on cancel", async () => {
    deleteProjectMock.mockResolvedValue(undefined as never);
    renderWithClient(<ProjectSessionTree />);
    await screen.findByTestId("project-row-other");
    fireEvent.mouseEnter(screen.getByTestId("project-row-other"));
    fireEvent.click(screen.getByRole("button", { name: "Delete other" }));
    fireEvent.click(await screen.findByTestId("confirm-delete-project-dialog-cancel"));
    expect(deleteProjectMock).not.toHaveBeenCalled();

    fireEvent.mouseEnter(screen.getByTestId("project-row-other"));
    fireEvent.click(screen.getByRole("button", { name: "Delete other" }));
    fireEvent.click(await screen.findByTestId("confirm-delete-project-dialog-confirm"));
    await waitFor(() => {
      expect(deleteProjectMock).toHaveBeenCalledWith("other");
    });
  });
});
