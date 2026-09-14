/**
 * ChildEscalationPreview mailbox tone (orchestrator-mailbox rule).
 *
 * Specialist notices (kind `escalation`) render as FYI for the
 * orchestrator — no options, a "no decision needed" hint, and a
 * "dismiss" action. Questions keep the answer options and the
 * "skip = deny" guard.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ChildEscalationPreview } from "../TurnDelegations";
import { api } from "@/api/client";
import type { EscalationRecord } from "@/types";

vi.mock("@/api/client", () => ({
  api: { getEscalation: vi.fn() },
}));

const getMock = vi.mocked(api.getEscalation);

function record(overrides?: Partial<EscalationRecord>): EscalationRecord {
  return {
    escalation_id: "esc-1",
    delegation_id: "d1",
    question: "blocked on X",
    options: null,
    kind: "escalation",
    audience: "orchestrator",
    status: "pending",
    created_at: "",
    deadline_at: null,
    answered_at: null,
    response: null,
    metadata: null,
    ...overrides,
  };
}

describe("ChildEscalationPreview mailbox tone", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders a pending notice as FYI with dismiss", async () => {
    getMock.mockResolvedValue(record());
    render(<ChildEscalationPreview delegationId="d1" />);
    await waitFor(() => expect(screen.getByTestId("turn-delegation-escalation")).toBeDefined());
    const card = screen.getByTestId("turn-delegation-escalation");
    expect(card.textContent).toContain("Notice");
    expect(card.textContent).toContain("blocked on X");
    expect(screen.getByTestId("turn-escalation-notice-hint").textContent).toContain(
      "no decision needed",
    );
    expect(screen.getByTestId("turn-escalation-skip").textContent).toBe("dismiss");
  });

  it("keeps options + skip-deny on pending questions", async () => {
    getMock.mockResolvedValue(
      record({
        kind: "question",
        audience: "human",
        question: "JWT or session?",
        options: ["JWT", "session"],
      }),
    );
    render(<ChildEscalationPreview delegationId="d1" />);
    await waitFor(() => expect(screen.getByTestId("turn-delegation-escalation")).toBeDefined());
    expect(screen.getAllByTestId("turn-escalation-option")).toHaveLength(2);
    expect(screen.getByTestId("turn-escalation-skip").textContent).toBe("skip = deny");
  });
});
