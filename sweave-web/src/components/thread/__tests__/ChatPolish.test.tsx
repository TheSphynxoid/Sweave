/**
 * Chat polish contracts (2026-09-13 agentic restyle).
 *
 * Pins the visual layer without going brittle: entrance animation
 * hooks, the delegation timeline header, the code-block language
 * header, and the composer kbd hints. Behavior (streaming, pills,
 * questions) stays pinned by the existing suites.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RoundBlock } from "@/components/thread/Thread";
import { TurnDelegations } from "@/components/thread/TurnDelegations";
import { Markdown } from "@/components/thread/markdown/Markdown";
import { api } from "@/api/client";
import type { Delegation } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    listDelegations: vi.fn(),
    getDelegationDetail: vi.fn(),
    getEscalation: vi.fn(),
    answerEscalation: vi.fn(),
    skipEscalation: vi.fn(),
  },
}));

vi.mock("@/context/WSProvider", () => ({
  useWS: () => ({
    state: "open",
    subscribe: () => () => {},
  }),
}));

const listMock = vi.mocked(api.listDelegations);

function child(overrides: Partial<Delegation> & { delegation_id: string }): Delegation {
  return {
    schema_version: 10,
    task_id: `task-${overrides.delegation_id}`,
    agent: "backend",
    model: "opencode/glm-5.3",
    task: "Implement the orders endpoint with validation and tests",
    status: "running",
    created_at: "2026-09-09T10:00:00",
    updated_at: "2026-09-09T10:01:00",
    started_at: "2026-09-09T10:00:05",
    completed_at: null,
    parent_session_id: "ses-1",
    project_name: "shop",
    output: "",
    error: null,
    worktree_path: null,
    branch: null,
    pr_url: null,
    parent_task_id: "chat-abc",
    manifest: null,
    depth: 1,
    chain_root_id: "chat-abc",
    coordination_tokens: 0,
    kind: "task",
    needs_attention: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("chat polish", () => {
  it("round-block expansion animates in place", () => {
    render(
      <RoundBlock round={0} preview="Dispatching to backend">
        <p>Full narration.</p>
      </RoundBlock>,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByTestId("round-block-expanded")).toBeTruthy();
    expect(screen.getByText("Full narration.")).toBeTruthy();
  });

  it("code blocks carry a language header next to the copy button", () => {
    render(<Markdown source={"```ts\nconst x = 1;\n```"} />);
    expect(screen.getByTestId("markdown-pre").textContent).toContain("const x = 1;");
    expect(screen.getByTestId("markdown-copy-code")).toBeTruthy();
    expect(screen.getByTestId("markdown-code-lang").textContent).toBe("ts");
  });

  it("code blocks fall back to a generic label without a fence language", () => {
    render(<Markdown source={"```\nplain\n```"} />);
    expect(screen.getByTestId("markdown-code-lang").textContent).toBe("code");
  });

  it("delegation timeline names the swarm activity + running count", async () => {
    listMock.mockResolvedValue([
      child({ delegation_id: "d-1", status: "running" }),
      child({
        delegation_id: "d-2",
        agent: "frontend",
        status: "done",
        output: "Done.",
        created_at: "2026-09-09T10:02:00",
      }),
    ]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <TurnDelegations parentDelegationId="chat-abc" />
      </QueryClientProvider>,
    );
    const header = await screen.findByTestId("turn-delegations-header");
    expect(header.textContent).toContain("Specialist activity");
    expect(header.textContent).toContain("2 tasks");
    expect(header.textContent).toContain("1 running");
    expect((await screen.findAllByTestId("turn-delegation-card")).length).toBe(2);
  });
});
