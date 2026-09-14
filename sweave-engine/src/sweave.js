// Native Sweave-tool calls (step 2). Same HTTP surface the MCP
// server uses (SWEAVE_HOST/PORT + X-Sweave-MCP-Token from
// SWEAVE_MCP_TOKEN or ~/.sweave/mcp_token) with IDENTICAL contract
// strings (queued:/rejected:/escalated:) — no MCP hop, no
// sweave_ prefix mangling. Deltas from the MCP transport are
// documented per call: ask_human BLOCKS inside the tool call (the
// engine owns the wait the ChatLoop owns on the opencode path) and
// returns the human's answer as the tool result.

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

export function sweaveApiBase() {
  if (process.env.SWEAVE_API_URL) return process.env.SWEAVE_API_URL.replace(/\/$/, "");
  const host = process.env.SWEAVE_HOST || "127.0.0.1";
  const port = process.env.SWEAVE_PORT || "8100";
  return `http://${host}:${port}`;
}

export function sweaveToken() {
  if (process.env.SWEAVE_MCP_TOKEN) return process.env.SWEAVE_MCP_TOKEN;
  try {
    const p = join(homedir(), ".sweave", "mcp_token");
    if (existsSync(p)) return readFileSync(p, "utf8").trim();
  } catch {}
  return null;
}

async function post(path, body, signal) {
  const token = sweaveToken();
  const resp = await fetch(`${sweaveApiBase()}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Sweave-MCP-Token": token } : {}),
    },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {}
  if (!resp.ok) {
    const detail = (data && (data.detail || data.message)) || text || `HTTP ${resp.status}`;
    const err = new Error(String(detail).slice(0, 500));
    err.status = resp.status;
    throw err;
  }
  return data || {};
}

async function get(path, signal) {
  const token = sweaveToken();
  const resp = await fetch(`${sweaveApiBase()}${path}`, {
    headers: { ...(token ? { "X-Sweave-MCP-Token": token } : {}) },
    ...(signal ? { signal } : {}),
  });
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {}
  if (!resp.ok) {
    const detail = (data && (data.detail || data.message)) || text || `HTTP ${resp.status}`;
    throw new Error(String(detail).slice(0, 500));
  }
  return data || {};
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const SWEAVE_TOOL_DEFS = [
  {
    name: "defer",
    description: "Hand implementation work to a specialist (queued; end your turn after — the call never blocks, your follow-up turn gets the results; do NOT poll by re-deferring).",
    parameters: {
      type: "object",
      properties: {
        target: { type: "string" },
        task: { type: "string" },
        reason: { type: "string" },
        estimate: { type: "object" },
        blocking: {
          type: "boolean",
          description:
            "true = your turn waits for this child and the follow-up turn synthesizes it; false = fire-and-forget into the Children lane. Omitted on a chat-turn defer joins by default.",
        },
      },
      required: ["target", "task"],
    },
  },
  {
    name: "list_specialists",
    description: "List available specialists (name -- description).",
    parameters: { type: "object", properties: {} },
  },
  {
    name: "ask_human",
    description: "Ask the human a blocking question (waits for the answer).",
    parameters: {
      type: "object",
      properties: {
        question: { type: "string" },
        options: { type: "array", items: { type: "string" } },
      },
      required: ["question"],
    },
  },
  {
    name: "escalate",
    description: "Send a non-blocking notice to the orchestrator.",
    parameters: {
      type: "object",
      properties: { message: { type: "string" } },
      required: ["message"],
    },
  },
];

/** Role gate (structural, never a deny-list): specialists reach only escalate. */
export function sweaveToolsFor(role) {
  if (role === "orchestrator") return SWEAVE_TOOL_DEFS;
  return SWEAVE_TOOL_DEFS.filter((t) => t.name === "escalate");
}

function rejectedPrefix(msg) {
  const low = msg.toLowerCase();
  return low.includes("loop detected") || low.includes("depth") || low.includes("budget");
}

export async function callDefer(args, runCtx) {
  const a = args || {};
  const target = a.target;
  const task = a.task;
  if (typeof target !== "string" || !target.trim()) {
    return { ok: false, text: "rejected: 'target' is required and must be a non-empty string" };
  }
  if (typeof task !== "string" || !task.trim()) {
    return { ok: false, text: "rejected: 'task' is required and must be a non-empty string" };
  }
  const caller = runCtx.delegationId;
  if (!caller) {
    return {
      ok: false,
      text: "rejected: 'caller_delegation_id' is required (the orchestrator's own delegation id; set it in the tool call)",
    };
  }
  if (a.estimate !== undefined && (typeof a.estimate !== "object" || a.estimate === null)) {
    return {
      ok: false,
      text: "rejected: 'estimate' must be an object like {tokens: 1000, seconds: 60} when present",
    };
  }
  if (a.blocking !== undefined && typeof a.blocking !== "boolean") {
    return {
      ok: false,
      text: "rejected: 'blocking' must be a boolean when present (true = join the synthesis wait-set, false/absent = fire-and-forget)",
    };
  }
  const body = { task, agent: target, parent_task_id: caller };
  if (a.reason) body.manifest = { intent: a.reason, source: "orchestrator_defer" };
  if (a.estimate !== undefined) body.estimate = a.estimate;
  if (a.blocking !== undefined) body.blocking = a.blocking;
  try {
    const data = await post("/api/v2/tasks", body, runCtx.signal);
    return { ok: true, text: `queued: ${data.delegation_id || "?"} (target=${target})` };
  } catch (e) {
    const msg = e.message || String(e);
    if (rejectedPrefix(msg)) return { ok: false, text: `rejected: ${msg}` };
    return { ok: false, text: `error: ${msg}` };
  }
}

export async function callListSpecialists() {
  try {
    const data = await get("/api/mcp/specialists");
    const specialists = data.specialists || [];
    if (!specialists.length) return { ok: true, text: "(no specialists available)" };
    return {
      ok: true,
      text: specialists
        .map((s) => `${s.name} -- ${(s.description || "").trim() || "(no description)"}`)
        .join("\n"),
    };
  } catch (e) {
    return { ok: false, text: `error: ${e.name || "Error"}: ${e.message}` };
  }
}

async function waitEscalation(delegationId, isAborted, signal) {
  for (;;) {
    if (isAborted()) throw new Error("aborted");
    if (signal && signal.aborted) throw Object.assign(new Error("aborted"), { code: "aborted" });
    await sleep(2000);
    let rec = null;
    try {
      rec = await get(`/api/delegations/${encodeURIComponent(delegationId)}/escalation`, signal);
    } catch (e) {
      if (e && e.code === "aborted") throw e;
      if (signal && signal.aborted) throw Object.assign(new Error("aborted"), { code: "aborted" });
      continue;
    }
    const status = rec && rec.status;
    if (status && status !== "pending") return rec;
  }
}

export async function callAskHuman(args, runCtx) {
  const a = args || {};
  if (typeof a.question !== "string" || !a.question.trim()) {
    return { ok: false, text: "rejected: 'question' is required and must be a non-empty string" };
  }
  if (a.options !== undefined && (!Array.isArray(a.options) || !a.options.every((o) => typeof o === "string"))) {
    return { ok: false, text: "rejected: 'options' must be a list of strings when provided" };
  }
  const caller = runCtx.delegationId;
  if (!caller) {
    return {
      ok: false,
      text: "rejected: 'caller_delegation_id' is required (the asking delegation's id; set it in the tool call)",
    };
  }
  const body = { question: a.question, caller_delegation_id: caller, kind: "question", audience: "human" };
  if (a.options) body.options = [...a.options];
  let escalationId = "?";
  try {
    const data = await post(`/api/delegations/${encodeURIComponent(caller)}/escalate`, body, runCtx.signal);
    escalationId = data.escalation_id || "?";
  } catch (e) {
    return { ok: false, text: `error: ${e.message}` };
  }
  // BLOCK inside the call (the engine owns the ChatLoop's hold-open
  // here): no timeout per the M1.11 ruling; abort-aware.
  try {
    const rec = await waitEscalation(caller, runCtx.isAborted, runCtx.signal);
    if (rec.status === "answered") {
      return { ok: true, text: `Human answer: ${rec.response || "(empty)"}` };
    }
    return { ok: true, text: `Question ${rec.status} — proceed with best judgment.` };
  } catch (e) {
    return { ok: false, text: `error: ${e.message}` };
  }
}

export async function callEscalate(args, runCtx) {
  const a = args || {};
  if (typeof a.message !== "string" || !a.message.trim()) {
    return { ok: false, text: "rejected: 'message' is required and must be a non-empty string" };
  }
  const caller = runCtx.delegationId;
  if (!caller) {
    return {
      ok: false,
      text: "rejected: 'caller_delegation_id' is required (the escalating delegation's id; set it in the tool call)",
    };
  }
  const body = {
    question: a.message.trim(),
    caller_delegation_id: caller.trim(),
    kind: "escalation",
    audience: "orchestrator",
  };
  try {
    const data = await post(`/api/delegations/${encodeURIComponent(caller.trim())}/escalate`, body, runCtx.signal);
    return {
      ok: true,
      text: `escalated: ${data.escalation_id || "?"} (to orchestrator; your turn continues — state the block in your summary too)`,
    };
  } catch (e) {
    return { ok: false, text: `error: ${e}` };
  }
}

/**
 * Permission ask round-trip: POST /api/engine/permission (the server
 * creates the blocking human escalation and waits) -> once|always|reject.
 * Abort-aware: a turn stop settles the wait immediately (the server
 * side resolves the escalation as skipped via the cancel path, so no
 * orphaned question survives either).
 */
export async function callEnginePermission({ delegationId, question, options, metadata }, isAborted, signal) {
  const aborted = new Promise((_, reject) => {
    if (!signal) return;
    if (signal.aborted) {
      reject(Object.assign(new Error("aborted"), { code: "aborted" }));
      return;
    }
    signal.addEventListener(
      "abort",
      () => reject(Object.assign(new Error("aborted"), { code: "aborted" })),
      { once: true }
    );
  });
  const data = await Promise.race([
    post("/api/engine/permission", {
      delegation_id: delegationId,
      question,
      options: options || ["allow once", "always allow", "deny"],
      metadata: metadata || {},
    }, signal),
    aborted,
  ]);
  void isAborted;
  return data.response || "reject";
}

export async function callSweaveTool(name, args, runCtx) {
  switch (name) {
    case "defer":
      return callDefer(args, runCtx);
    case "list_specialists":
      return callListSpecialists();
    case "ask_human":
      return callAskHuman(args, runCtx);
    case "escalate":
      return callEscalate(args, runCtx);
    default:
      return { ok: false, text: `rejected: unknown sweave tool: ${name}` };
  }
}
