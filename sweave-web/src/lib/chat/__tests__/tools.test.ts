/**
 * Chat tool transparency (opencode-style tool rows): adapter
 * state-machine tests.
 *
 * Pins: `chat.tool` upserts rows per callID (latest status wins),
 * early-bubble creation when tools precede text, the trailing-garbage
 * guard (no rows after settle), persisted `metadata.tools`
 * projection, and turn-snapshot recovery.
 */
import { describe, it, expect } from "vitest";
import {
  applyDelta,
  applyMessageAdded,
  applyTool,
  applyTurnSnapshot,
  initialThreadState,
  normalizeToolRow,
  projectEntry,
  projectThread,
  toolsOf,
} from "../runtime";
import type { SessionMessage, TurnSnapshot } from "@/types";

function assistantMessage(
  id: string,
  content: string,
  delegationId?: string,
  tools?: unknown,
): SessionMessage {
  return {
    id,
    role: "assistant",
    content,
    timestamp: "2026-09-07T00:00:00Z",
    agent: "orchestrator",
    tool_name: null,
    tool_result: null,
    metadata: {
      ...(delegationId ? { delegation_id: delegationId } : {}),
      ...(tools !== undefined ? { tools } : {}),
    },
  };
}

function turnSnapshot(overrides: Partial<TurnSnapshot> = {}): TurnSnapshot {
  return {
    session_id: "s1",
    delegation_id: "chat-1",
    status: "running",
    phase: "streaming",
    started_at: "2026-09-07T00:00:00Z",
    stream_text: "partial",
    thinking_text: "",
    pending_question: false,
    round: 0,
    ...overrides,
  };
}

const READ_PENDING = {
  callID: "c1",
  tool: "read",
  status: "pending",
  summary: "src/foo.ts",
  title: null,
  input: null,
  round: 0,
};

const READ_DONE = { ...READ_PENDING, status: "completed" };

describe("normalizeToolRow", () => {
  it("drops rows without a callID", () => {
    expect(normalizeToolRow(null)).toBeNull();
    expect(normalizeToolRow({})).toBeNull();
    expect(normalizeToolRow({ tool: "read" })).toBeNull();
  });

  it("degrades unknown shapes to a best-effort row", () => {
    const row = normalizeToolRow({ callID: "c1" });
    expect(row).toMatchObject({ callID: "c1", tool: "tool", status: "unknown" });
  });
});

describe("applyTool: live rows", () => {
  it("creates the bubble when tools precede the first text delta", () => {
    const s = applyTool(initialThreadState(), "chat-1", READ_PENDING);
    expect(s.turn).toBe("running");
    expect(s.activeDelegationId).toBe("chat-1");
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].streaming).toBe(true);
    expect(s.entries[0].message.content).toBe("");
    expect(s.entries[0].tools).toHaveLength(1);
  });

  it("upserts by callID: latest status wins, no duplicate rows", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_PENDING);
    s = applyTool(s, "chat-1", READ_DONE);
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].tools).toHaveLength(1);
    expect(s.entries[0].tools![0].status).toBe("completed");
  });

  it("accumulates distinct callIDs in arrival order", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_PENDING);
    s = applyTool(s, "chat-1", { ...READ_PENDING, callID: "c2", tool: "bash", summary: "ls" });
    expect(s.entries[0].tools!.map((t) => t.callID)).toEqual(["c1", "c2"]);
  });

  it("scopes rows by round", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_PENDING, 0);
    s = applyTool(s, "chat-1", READ_PENDING, 1);
    expect(s.entries).toHaveLength(2);
  });

  it("ignores tool events after the delegation settled (trailing garbage)", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_DONE);
    s = applyMessageAdded(s, assistantMessage("a1", "final", "chat-1"));
    const settled = s;
    const after = applyTool(settled, "chat-1", READ_PENDING);
    expect(after).toBe(settled);
  });
});

describe("toolsOf + projection", () => {
  it("reads persisted metadata.tools, dropping rows without callID", () => {
    const msg = assistantMessage("a1", "done", "chat-1", [
      { callID: "c1", tool: "read", status: "completed", summary: "x.ts" },
      { tool: "no-id" },
      "garbage",
    ]);
    expect(toolsOf(msg).map((t) => t.callID)).toEqual(["c1"]);
  });

  it("returns [] when metadata.tools is absent", () => {
    expect(toolsOf(assistantMessage("a1", "done"))).toEqual([]);
  });

  it("projects live tools onto the streaming bubble, persisted onto settled", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_DONE);
    const live = projectEntry(s.entries[0]);
    expect(
      (live!.metadata as { custom: { tools: { callID: string }[] } }).custom.tools,
    ).toHaveLength(1);
    s = applyMessageAdded(s, assistantMessage("a1", "final", "chat-1", [READ_DONE]));
    const settled = projectEntry(s.entries[s.entries.length - 1]);
    expect(
      (settled!.metadata as { custom: { tools: { callID: string }[] } }).custom.tools,
    ).toHaveLength(1);
  });
});

describe("turn recovery: snapshot tools", () => {
  it("seeds the recovering bubble with snapshot tools", () => {
    const s = applyTurnSnapshot(initialThreadState(), turnSnapshot({ tools: [READ_DONE] }));
    expect(s.entries).toHaveLength(1);
    expect(s.entries[0].tools).toHaveLength(1);
    expect(s.entries[0].tools![0].status).toBe("completed");
  });

  it("unions snapshot-only rows into a live bubble; live rows win", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_DONE);
    s = applyTurnSnapshot(
      s,
      turnSnapshot({
        stream_text: "older partial",
        tools: [
          { ...READ_PENDING, status: "pending" },
          { ...READ_PENDING, callID: "c0", summary: "missed.ts" },
        ],
      }),
    );
    const byId = new Map(s.entries[0].tools!.map((t) => [t.callID, t.status]));
    // Live completed row wins over the older pending snapshot for c1…
    expect(byId.get("c1")).toBe("completed");
    // …while the missed c0 transition backfills.
    expect(byId.get("c0")).toBe("pending");
  });
});

describe("live op log: ordered timeline without layout jump", () => {
  it("tracks text/thinking/tool arrivals in order on the bubble", () => {
    let s = applyDelta(initialThreadState(), "chat-abc", "First. ");
    s = applyTool(s, "chat-abc", READ_DONE);
    s = applyDelta(s, "chat-abc", "Second.");
    expect(s.entries[0].seq).toEqual([
      { kind: "text", text: "First. " },
      { kind: "tool", callID: "c1" },
      { kind: "text", text: "Second." },
    ]);
  });

  it("takes only one timeline position per callID (status updates refresh)", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_PENDING);
    s = applyTool(s, "chat-1", READ_DONE);
    expect(s.entries[0].seq).toEqual([{ kind: "tool", callID: "c1" }]);
  });

  it("projects the live log as segments so streaming renders interleaved", () => {
    let s = applyDelta(initialThreadState(), "chat-abc", "First. ");
    s = applyTool(s, "chat-abc", READ_DONE);
    const live = projectEntry(s.entries[0]);
    const custom = (live!.metadata as { custom: { segments: unknown[] } }).custom;
    expect(custom.segments).toEqual([
      { kind: "text", text: "First. " },
      { kind: "tool", callID: "c1" },
    ]);
  });

  it("appends snapshot-only tool markers after the live log on recovery", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_DONE);
    s = applyTurnSnapshot(
      s,
      turnSnapshot({
        stream_text: "older partial",
        tools: [{ ...READ_PENDING, callID: "c0", summary: "missed.ts" }],
      }),
    );
    expect(s.entries[0].seq).toEqual([
      { kind: "tool", callID: "c1" },
      { kind: "tool", callID: "c0" },
    ]);
  });
});

describe("active-turn projection: lanes survive the round gap", () => {
  it("marks the live turn's bubbles active; settled turns idle", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_DONE);
    let projected = projectThread(s);
    expect(
      (projected[0].metadata as { custom: { isActiveTurn: boolean } }).custom
        .isActiveTurn,
    ).toBe(true);
    s = applyMessageAdded(s, assistantMessage("a1", "final", "chat-1"));
    projected = projectThread(s);
    expect(
      (projected[0].metadata as { custom: { isActiveTurn: boolean } }).custom
        .isActiveTurn,
    ).toBe(false);
  });

  it("never marks user messages active", () => {
    let s = applyTool(initialThreadState(), "chat-1", READ_DONE);
    const user = {
      id: "u1",
      role: "user" as const,
      content: "hi",
      timestamp: "",
      agent: null,
      tool_name: null,
      tool_result: null,
      metadata: {},
    };
    s = { ...s, entries: [{ message: user, streaming: false }, ...s.entries] };
    const projected = projectThread(s);
    expect(
      (projected[0].metadata as { custom: { isActiveTurn: boolean } }).custom
        .isActiveTurn,
    ).toBe(false);
  });
});
