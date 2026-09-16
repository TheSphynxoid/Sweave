/**
 * DetailView tabbed surface tests (rescoped Step 4a + Q2 reversal).
 *
 * The tab *content* components live in `./detail/sections.tsx` and
 * are container-agnostic (no modal, no portal — ruling Q6), so the
 * most precise unit boundary is to test those directly. The modal
 * chrome (`DetailView.tsx`) gets a mount/Overview smoke test (all
 * five triggers render; the default Overview content mounts; the
 * Transcript tab degrades to the frozen-state copy when there is no
 * transcript).
 *
 * Pins:
 *   - ToolsSection renders newest-first (ruling Q2) — the
 *     chronological wire order is reversed at render, never on the
 *     backend;
 *   - TranscriptSection stays chronological (conversation order) and
 *     degrades to the frozen-state copy when absent/empty (no empty
 *     promise, no crash);
 *   - Prompt/Overview/Tokens sections render their sections.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DetailView } from "@/pages/children/DetailView";
import {
  OverviewTab,
  PromptTab,
  ToolsTab,
  TokensStatusTab,
  TranscriptTab,
  FrozenStateNotice,
} from "@/pages/children/detail/sections";
import { api } from "@/api/client";
import type { DelegationDetail, ToolTimelineEntry, TranscriptBlock } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    getDelegationDetail: vi.fn(),
    getEscalation: vi.fn(),
  },
}));

const detailMock = vi.mocked(api.getDelegationDetail);
const escMock = vi.mocked(api.getEscalation);

beforeEach(() => {
  vi.clearAllMocks();
  escMock.mockResolvedValue(null);
});

// ---------------------------------------------------------------------------
// Container-agnostic section components (Q6 — the dockable boundary)
// ---------------------------------------------------------------------------

function tool(callID: string, status: string): ToolTimelineEntry {
  return { callID, tool: "Bash", status, started_at: null, states: [] };
}

const wiresTools: ToolTimelineEntry[] = [
  tool("t-old", "completed"),
  tool("t-new", "running"),
  tool("t-mid", "completed"),
];

describe("ToolsTab (ruling Q2 — newest-first)", () => {
  it("reverses the chronological wire order so newest is on top", () => {
    // wiresTools is chronological (oldest first): t-old, t-new, t-mid.
    // Newest-first => [t-mid, t-new, t-old].
    render(<ToolsTab tools={wiresTools} />);
    const rows = screen.getAllByTestId(/^tool-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "tool-t-mid",
      "tool-t-new",
      "tool-t-old",
    ]);
    expect(screen.getByTestId("tools-tab").textContent).toContain("newest first");
  });

  it("does not mutate the input array (reverse is render-only)", () => {
    const input = [...wiresTools];
    render(<ToolsTab tools={input} />);
    // The source array must keep chronological order.
    expect(input.map((t) => t.callID)).toEqual(["t-old", "t-new", "t-mid"]);
  });

  it("empty tools shows the no-calls copy", () => {
    render(<ToolsTab tools={[]} />);
    expect(screen.getByText("No tool calls in this delegation.")).toBeTruthy();
  });
});

describe("TranscriptTab (chronological; degrades gracefully)", () => {
  it("renders blocks in conversation order", () => {
    const blocks: TranscriptBlock[] = [
      { id: "b1", role: "prompt", prompt: "First turn prompt" },
      { id: "b2", role: "assistant", text: "Second turn reply" },
      { id: "b3", role: "assistant", text: "Third turn reply" },
    ];
    render(<TranscriptTab transcript={blocks} />);
    const rows = screen.getAllByTestId(/^transcript-block-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "transcript-block-b1",
      "transcript-block-b2",
      "transcript-block-b3",
    ]);
    expect(screen.getByText("First turn prompt")).toBeTruthy();
    expect(screen.getByText("Second turn reply")).toBeTruthy();
  });

  it("absent transcript shows the frozen-state copy (no empty promise)", () => {
    render(<TranscriptTab transcript={null} />);
    expect(screen.getByTestId("transcript-frozen").textContent).toContain(
      "No output yet — tool running, bounded.",
    );
    expect(screen.queryByTestId("transcript-tab")).toBeNull();
  });

  it("empty transcript array shows the frozen-state copy", () => {
    render(<TranscriptTab transcript={[]} />);
    expect(screen.getByTestId("transcript-frozen")).toBeTruthy();
  });

  it("FrozenStateNotice copy is the 4c replacement for the dead bubble", () => {
    render(<FrozenStateNotice />);
    expect(screen.getByTestId("transcript-frozen").textContent).toContain(
      "No output yet — tool running, bounded.",
    );
  });
});

describe("PromptTab / OverviewTab / TokensStatusTab", () => {
  it("PromptTab renders the composed-prompt audit", () => {
    render(
      <PromptTab
        composed={{
          memory_chars: 10,
          whats_new_chars: 20,
          synthesis_chars: 30,
          transcript_ref_chars: 40,
          user_chars: 50,
          dropped_memory: 0,
          dropped_whats_new: 0,
          dropped_synthesis: 0,
        }}
      />,
    );
    expect(screen.getByTestId("prompt-tab")).toBeTruthy();
    expect(screen.getByText("memory")).toBeTruthy();
  });

  it("TokensStatusTab renders tokens + chronological status timeline", () => {
    const detail: DelegationDetail = {
      delegation_id: "d1",
      composed_prompt: null,
      tool_timeline: [],
      tokens: { input: 1, output: 2, reasoning: 3, cache_read: 4, cache_write: 5, cost: 0.01 },
      status_timeline: [
        { status: "queued", source: "harness", ts: "2026-09-09T10:00:00" },
        { status: "running", source: "harness", ts: "2026-09-09T10:00:05" },
      ],
    };
    render(<TokensStatusTab tokens={detail.tokens} timeline={detail.status_timeline} />);
    expect(screen.getByTestId("tokens-tab")).toBeTruthy();
    expect(screen.getByTestId("status-timeline-tab")).toBeTruthy();
    const items = screen.getByTestId("status-timeline-tab").querySelectorAll("li");
    // Chronological (unchanged by Q2).
    expect(items[0].textContent).toContain("queued");
    expect(items[1].textContent).toContain("running");
  });

  it("OverviewTab renders identity summary + escalation", () => {
    const detail: DelegationDetail = {
      delegation_id: "d-x",
      composed_prompt: null,
      tool_timeline: [],
      tokens: null,
      status_timeline: [],
    };
    const esc = {
      escalation_id: "e1",
      delegation_id: "d-x",
      question: "Permission required?",
      options: ["allow"],
      kind: "permission" as const,
      audience: "human" as const,
      status: "pending" as const,
      created_at: "2026-09-10T23:27:54",
      deadline_at: null,
      answered_at: null,
      response: null,
    };
    render(<OverviewTab delegationId="d-x" escalation={esc} detail={detail} />);
    expect(screen.getByTestId("identity-summary")).toBeTruthy();
    expect(screen.getByTestId("escalation-section").textContent).toContain("Permission required?");
  });
});

// ---------------------------------------------------------------------------
// Modal chrome (DetailView.tsx) — mount + default tab + degrade
// ---------------------------------------------------------------------------

function renderDetail(id = "d1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DetailView delegationId={id} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

function baseDetail(overrides: Partial<DelegationDetail> = {}): DelegationDetail {
  return {
    delegation_id: "d1",
    composed_prompt: null,
    tool_timeline: wiresTools,
    tokens: null,
    status_timeline: [
      { status: "queued", source: "harness", ts: "2026-09-09T10:00:00" },
      { status: "running", source: "harness", ts: "2026-09-09T10:00:05" },
    ],
    transcript: null,
    ...overrides,
  };
}

describe("DetailView modal chrome", () => {
  it("mounts the portal with all five tab triggers and the default Overview content", async () => {
    detailMock.mockResolvedValue(baseDetail());
    renderDetail();
    expect(await screen.findByTestId("detail-modal")).toBeTruthy();
    expect(await screen.findByTestId("tab-overview")).toBeTruthy();
    expect(screen.getByTestId("tab-transcript")).toBeTruthy();
    expect(screen.getByTestId("tab-tools")).toBeTruthy();
    expect(screen.getByTestId("tab-prompt")).toBeTruthy();
    expect(screen.getByTestId("tab-tokens")).toBeTruthy();
    expect(await screen.findByTestId("identity-summary")).toBeTruthy();
    expect(detailMock).toHaveBeenCalledWith("d1");
  });

  it("initialTab=transcript opens at the Transcript tab content (4b affordance)", async () => {
    detailMock.mockResolvedValue(
      baseDetail({
        transcript: [{ id: "b1", role: "assistant", text: "hi" }],
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DetailView delegationId="d1" onClose={() => {}} initialTab="transcript" />
      </QueryClientProvider>,
    );
    await screen.findByTestId("detail-modal");
    // The Transcript tab is active on open (Radix marks it data-state=active).
    const content = await screen.findByTestId("tabcontent-transcript");
    expect(content.getAttribute("data-state")).toBe("active");
    expect(screen.getByTestId("transcript-block-b1")).toBeTruthy();
  });
});
