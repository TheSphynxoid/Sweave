// Agentic loop (step 2). The engine owns execution only: the
// orchestrator's composed prompt arrives as the user message, the
// loop drives provider tool-calls against the worktree cwd, and the
// finished text returns as the turn output.
//
// Execution model: single-threaded async (structured iterations);
// blocking bash runs as a non-blocking child process with a captured
// partial-output timeout; fs tools are microsecond promise ops. A
// wedged native call trips its per-tool timeout, never the loop (a
// stuck loop wedges EVERY session — the failure mode subprocesses
// never had). Provider streaming stays native: text deltas forward
// as token events while tool_call deltas accumulate per index.
//
// Doom-loop: 3rd consecutive identical call is rejected WITHOUT
// executing (typed error the model can adjust to — the native
// direction; opencode routes this through a permission ask).

import { newMessageId } from "./sessions.js";
import { resolve as resolvePath, sep, dirname, relative } from "node:path";
import {
  providerHttpError,
  withProviderRetry,
  DEFAULT_MAX_RETRIES,
} from "./retry.js";
import {
  EXEC_TOOL_DEFS,
  PER_TOOL_BUDGET_MS,
  executeTool,
  gateToolCall,
  matchTarget,
  permissionKey,
} from "./tools.js";
import { callEnginePermission, callSweaveTool, sweaveToolsFor } from "./sweave.js";
import { ENGINE_USER_AGENT, SESSION_HEADER } from "./providers.js";
import {
  historyToResponsesInput,
  providerResponsesStream,
} from "./responses.js";

const MAX_ITERATIONS = 50;
const DOOM_REPEATS = 3;
// Role-aware ceiling (2026-09-14): the flat 50 killed healthy
// implementation turns (two confirmed max_steps deaths on
// succeeding read/edit/probe loops). Specialists doing
// implementation get 3x headroom; the orchestrator's read-only
// turns never needed more than ~20 observed. The ceiling stays a
// cost backstop — stuckness trips earlier via NO_PROGRESS_LIMIT.
const MAX_ITERATIONS_SPECIALIST = 150;
// Stuckness trip (2026-09-14): consecutive tool iterations with
// zero successes (every executed call errored/denied/rejected).
// Catches the A-B-A-B alternation the identical-call doom guard
// misses. Thinking-only iterations break the streak (reconsidering
// is not stuck); successful sweave-tool calls (defer/escalate)
// count as progress (handing work off is forward motion).
const NO_PROGRESS_LIMIT = 5;

function approxTokens(text) {
  if (!text) return 0;
  return Math.max(1, Math.floor(String(text).split(/\s+/).length * 4 / 3));
}

/**
 * Settles (rejects with an `aborted` error) the moment `signal`
 * fires; never settles otherwise. Races tool execution so a turn
 * stop can't wait out a long tool — the kill is prompt, and the
 * tool's own signal handling stops the underlying work.
 */
function abortThrow(signal) {
  return new Promise((_, reject) => {
    if (!signal) return; // no signal: never settles, race keeps the tool
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
}

function withTimeout(promise, ms, onTimeout) {
  let timer = null;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve({ __timeout: true }), ms);
  });
  return Promise.race([promise, timeout]).then((v) => {
    clearTimeout(timer);
    if (v && v.__timeout) return onTimeout();
    return v;
  });
}

export function historyToProviderMessages(entries) {
  const out = [];
  for (const m of entries) {
    if (m.role === "user") {
      out.push({ role: "user", content: m.content || "" });
    } else if (m.role === "assistant" && !m.failed) {
      if (m.toolCalls && m.toolCalls.length > 0) {
        out.push({
          role: "assistant",
          content: m.content || "",
          tool_calls: m.toolCalls.map((tc) => ({
            id: tc.id,
            type: "function",
            function: { name: tc.name, arguments: JSON.stringify(tc.args || {}) },
          })),
        });
      } else {
        out.push({ role: "assistant", content: m.content || "" });
      }
    } else if (m.role === "tool") {
      out.push({ role: "tool", tool_call_id: m.toolCallId, content: m.content || "" });
    }
    // failed assistant entries stay out of provider history (their
    // error text already surfaced as the turn error).
  }
  return out;
}

export function toolDefsFor(body) {
  const execNames = new Set(body.tools || []);
  const exec = EXEC_TOOL_DEFS.filter((d) => execNames.has(d.name));
  const sweave = sweaveToolsFor(body.role === "orchestrator" ? "orchestrator" : "specialist");
  return { exec, sweave, all: [...exec, ...sweave] };
}

export function needsLoop(body) {
  if ((body.tools || []).length > 0) return true;
  if (body.role === "orchestrator") return true; // sweave tools offered
  return false;
}

async function providerStream({ baseURL, key, provider, modelId, flavor, sessionId, messages, defs, signal, onToken, onReasoning, onToolDelta, maxRetries, logPrefix }) {
  // `messages` is flavor-appropriate input (chat messages or Responses
  // input items — the caller maps history for the resolved flavor).
  // Both transports return { text, calls: [{id, name, args}], usage }.
  //
  // Transient provider/network failures retry inside the turn
  // (opencode retry.ts lesson): the SAME history replays, no session
  // rotation, no duplicate user message. Caveat: tokens streamed
  // before a mid-stream cut were already forwarded — a retry replays
  // the prefix live, but the returned (persisted) text is exactly the
  // final attempt's. Auth/bad-request/context errors never retry.
  const runOnce = async () => {
    if (flavor === "responses") {
      return providerResponsesStream({
        baseURL,
        key,
        modelId,
        sessionId,
        input: messages,
        defs,
        signal,
        onToken,
        onReasoning,
      });
    }
    return providerChatStream({
      baseURL,
      key,
      provider,
      modelId,
      sessionId,
      messages,
      defs,
      signal,
      onToken,
      onReasoning,
    });
  };
  return withProviderRetry(runOnce, {
    maxRetries: maxRetries ?? DEFAULT_MAX_RETRIES,
    signal,
    onRetry: ({ attempt, waitMs, error }) => {
      try {
        process.stderr.write(
          `${logPrefix || "sweave-engine"}: provider retry ${attempt} in ${waitMs}ms (${String((error && error.message) || error).slice(0, 160)})\n`
        );
      } catch {}
    },
  });
}

async function providerChatStream({ baseURL, key, provider, modelId, sessionId, messages, defs, signal, onToken, onReasoning, onToolDelta }) {
  const resp = await fetch(`${baseURL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key || "no-key"}`,
      "User-Agent": ENGINE_USER_AGENT,
      [SESSION_HEADER]: sessionId || "unknown",
      ...(provider === "openrouter"
        ? { "HTTP-Referer": "https://github.com/sweave", "X-Title": "Sweave Engine" }
        : {}),
    },
    body: JSON.stringify({
      model: modelId,
      messages,
      stream: true,
      stream_options: { include_usage: true },
      ...(defs.length > 0 ? { tools: defs.map((d) => ({ type: "function", function: d })) } : {}),
    }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw providerHttpError(resp.status, resp.headers, text.slice(0, 300));
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let text = "";
  const toolDeltas = new Map(); // index -> { id, name, arguments }
  let usage = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (payload === "[DONE]") continue;
      let obj;
      try {
        obj = JSON.parse(payload);
      } catch {
        continue;
      }
      const delta = obj?.choices?.[0]?.delta || {};
      if (typeof delta.content === "string" && delta.content) {
        text += delta.content;
        onToken(delta.content);
      }
      // Thinking capture: reasoning deltas ride the same stream in a
      // provider-specific field (see extractReasoningDelta). Forwarded
      // as-is; never mixed into `text` (the turn output).
      const rtext = extractReasoningDelta(delta);
      if (rtext && onReasoning) onReasoning(rtext);
      for (const tc of delta.tool_calls || []) {
        const slot = toolDeltas.get(tc.index ?? 0) || { id: "", name: "", arguments: "" };
        if (tc.id) slot.id = tc.id;
        if (tc.type) slot.type = tc.type;
        if (tc.function?.name) slot.name += tc.function.name;
        if (tc.function?.arguments) slot.arguments += tc.function.arguments;
        toolDeltas.set(tc.index ?? 0, slot);
      }
      if (obj?.usage) usage = obj.usage;
    }
  }
  const calls = [...toolDeltas.values()]
    .filter((c) => c.name)
    .map((c, i) => {
      let args = {};
      try {
        args = c.arguments ? JSON.parse(c.arguments) : {};
      } catch {
        args = { _raw: c.arguments };
      }
      return { id: c.id || `call_${Date.now().toString(36)}_${i}`, name: c.name, args };
    });
  if (onToolDelta) onToolDelta(calls);
  return { text, calls, usage };
}

let callCounter = 0;
function newCallId() {
  callCounter += 1;
  return `call_${Date.now().toString(36)}_${callCounter}`;
}

function approvalMatches(approvals, permission, target) {
  return (approvals || []).some((a) => {
    if (!a || a.permission !== permission) return false;
    // Legacy exact grants (pre-folder semantics) + the "*" escape.
    if (a.pattern === "*") return true;
    if (typeof a.pattern === "string" && a.pattern && a.pattern === target) {
      return true;
    }
    // Folder grants (2026-09-15 ruling): an always-allow covers the
    // asked path's containing folder and everything under it.
    if (typeof a.folder === "string" && a.folder && folderCovers(a.folder, target)) {
      return true;
    }
    return false;
  });
}

// True when `target` is `folder` itself or descends from it.
// Separator-normalized, case-insensitive on Windows (opencode parity:
// the engine evaluates what the map means, never policy itself).
function folderCovers(folder, target) {
  const norm = (p) => {
    const s = String(p || "").replace(/\//g, sep).replace(new RegExp(`\\${sep}+$`), "");
    return process.platform === "win32" ? s.toLowerCase() : s;
  };
  const f = norm(folder);
  const t = norm(target);
  if (!f || !t) return false;
  return t === f || t.startsWith(f + sep);
}

// True when `p` sits inside `root` (or is root). Used to bound an
// always-grant to the project subtree — outside-project targets stay
// exact-path (fail closed).
function insideRoot(root, p) {
  if (!root || !p) return false;
  const rel = relative(String(root), String(p));
  return rel !== "" ? !rel.startsWith("..") && resolvePath(rel) !== rel : true;
}

// Always-grants live HERE, never on the session object: per-run +
// per-specialist by construction (memory only — a restart wipes the
// map, and each engine session id has its own list). The session
// journal (sessions.json) must never see them; SessionStore scrubs
// legacy `approvals` arrays on load for the same guarantee.
const sessionApprovals = new Map();

function approvalsFor(sessionId) {
  let list = sessionApprovals.get(sessionId);
  if (!list) {
    list = [];
    sessionApprovals.set(sessionId, list);
  }
  return list;
}

/**
 * Reasoning-delta extractor (vercel/ai-pattern baseline, pinned
 * v7.0.99 — re-implemented, no dependency). Providers disagree on
 * the chat-completions field: DeepSeek-native sends
 * `reasoning_content`, OpenRouter sends `reasoning` plus a
 * structured `reasoning_details` array carrying the same text.
 * Structured details win when present (they distinguish text vs
 * summary and let us skip opaque `encrypted` entries); otherwise
 * first non-empty string wins so thinking is never doubled.
 */
export function extractReasoningDelta(delta) {
  if (!delta || typeof delta !== "object") return "";
  const details = delta.reasoning_details;
  if (Array.isArray(details)) {
    let out = "";
    for (const d of details) {
      if (!d || typeof d !== "object") continue;
      if (typeof d.text === "string") out += d.text;
      else if (typeof d.summary === "string") out += d.summary;
    }
    if (out) return out;
  }
  if (typeof delta.reasoning === "string" && delta.reasoning) return delta.reasoning;
  if (typeof delta.reasoning_content === "string" && delta.reasoning_content) {
    return delta.reasoning_content;
  }
  return "";
}

async function resolveAsk(execCtx, gate, toolName, callId, input) {
  const { session, emit, sweaveCtx, isAborted } = execCtx;
  if (approvalMatches(approvalsFor(session.id), gate.permission, gate.patterns[0])) {
    return "once";
  }
  const requestId = `eng_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e6)}`;
  emit({ event: "permission.asked", request_id: requestId, permission: gate.permission, patterns: gate.patterns, tool: toolName, call_id: callId, input });
  const delegationId = sweaveCtx.delegationId;
  if (!delegationId) return "reject"; // no delegation, no human: fail closed
  // Folder grant (2026-09-15 ruling): "always" on a path-keyed
  // external_directory ask covers the asked path's containing folder
  // and everything under it — per-file grants are too trashy.
  // Bounded to the project subtree; outside-project targets stay
  // exact-path (fail closed). Non-path permissions keep exact grants.
  let folderGrant = null;
  if (gate.permission === "external_directory" && gate.patterns[0]) {
    const folder = dirname(String(gate.patterns[0]));
    if (sweaveCtx.projectDir && insideRoot(sweaveCtx.projectDir, folder)) {
      folderGrant = folder;
    }
  }
  const question =
    `Engine asks ${gate.permission} for ${gate.patterns.join(", ")}` +
    (toolName === "bash" ? ` (command: ${JSON.stringify(input.command || "").slice(0, 300)})` : "") +
    (folderGrant
      ? `. 'Always allow' covers ${folderGrant} and everything under it`
      : "") +
    ". Answer 'allow once' / 'always allow' / 'deny' (or skip = deny).";
  const response = await callEnginePermission(
    { delegationId, question, options: ["allow once", "always allow", "deny"], metadata: { requestId, permission: gate.permission, patterns: gate.patterns, tool: toolName } },
    isAborted,
    sweaveCtx.signal
  );
  // NOTE: compare the MAPPED answer, not the raw string — the human
  // picks the multi-word "always allow" option, which never `===`
  // "always" (the pre-folder bug that made always-allow store
  // nothing at all, for any permission).
  if (mapPermissionResponse(response) === "always") {
    // Per-run + per-specialist (2026-09-15 ruling): grants live in
    // the memory-only map keyed by engine session id — never on the
    // session object (the journal must not persist them; a restart
    // wipes them and one specialist's grant never leaks to another).
    const list = approvalsFor(session.id);
    if (folderGrant) {
      if (!list.some((a) => a.permission === gate.permission && a.folder === folderGrant)) {
        list.push({ permission: gate.permission, folder: folderGrant });
      }
    } else if (!list.some((a) => a.permission === gate.permission && a.pattern === gate.patterns[0])) {
      list.push({ permission: gate.permission, pattern: gate.patterns[0] });
    }
  }
  return response;
}

function mapPermissionResponse(response) {
  const low = String(response || "").trim().toLowerCase();
  if (low.includes("always")) return "always";
  if (low.includes("deny") || low.includes("reject") || low === "no" || low === "skip") return "reject";
  return "once";
}

const SWEAVE_TOOL_NAMES = new Set(["defer", "list_specialists", "ask_human", "escalate"]);

/**
 * Run the agentic loop for one turn. Emits token, tool lifecycle,
 * step.boundary, permission.asked, done and tokens_used events via
 * emit(); appends the finished exchange to the session store.
 */
export async function runLoop(loopCtx) {
  const { session, saveSession, store, emit, body, resolved, cwd, signal, isAborted } = loopCtx;
  const model = body.model;
  const { exec, sweave, all } = toolDefsFor(body);
  const execNames = new Set(exec.map((d) => d.name));
  const offeredSweave = new Set(sweave.map((d) => d.name));
  const sweaveCtx = {
    delegationId: body.delegation_id || null,
    // Project root for bounding always-grants (additive wire field;
    // absent keeps the old exact-path behavior — fail closed).
    projectDir: body.project_dir || null,
    isAborted,
    signal,
  };
  const execCtxBase = { session, saveSession, emit, sweaveCtx, isAborted, cwd, signal };

  let totalIn = 0;
  let totalOut = 0;
  let totalReason = 0;
  let totalCacheRead = 0;
  // Peak single-step prompt_tokens in this turn (= the live context
  // size at its largest). totalIn SUMS cumulative per-step prompts
  // (each iteration re-bills full history — honest billing, but
  // steps×context, not size); maxIn is the fire-risk number
  // (compaction triggers read this, never the sum).
  let maxIn = 0;
  let lastRepeat = { key: "", count: 0 };
  let finalText = "";
  let iterations = 0;
  // Trip state (2026-09-14 rework): role-aware ceiling +
  // stuckness streak + resumption handoff. toolCallCount counts
  // every processed call; filesTouched collects executed file
  // targets in first-seen order (capped) for the handoff record.
  const maxIterations = body.role === "orchestrator" ? MAX_ITERATIONS : MAX_ITERATIONS_SPECIALIST;
  let noProgressStreak = 0;
  let toolCallCount = 0;
  const filesTouched = [];
  const noteFile = (p) => {
    if (!p || filesTouched.length >= 50 || filesTouched.includes(p)) return;
    filesTouched.push(p);
  };
  // Resumption handoff (graceful trip): the turn still fails loud
  // (fail-loud ruling — no silent continuation), but the trace keeps
  // a machine-readable record of what ran so a follow-up turn (or a
  // human) resumes instead of re-walking the ground. Turn totals
  // ride along (per-iteration tokens are already on the boundary).
  const handoffPayload = (reason) => ({
    event: "step.boundary",
    reason,
    cost: 0,
    tokens: { input: totalIn, output: totalOut, reasoning: totalReason, cache: { read: totalCacheRead, write: 0 }, context_input: maxIn },
    handoff: {
      iterations,
      toolCalls: toolCallCount,
      filesTouched: [...filesTouched],
    },
  });

  for (;;) {
    if (isAborted()) throw Object.assign(new Error("aborted"), { code: "aborted" });
    if (iterations >= maxIterations) {
      emit(handoffPayload("max_steps"));
      throw Object.assign(new Error(`max loop iterations (${maxIterations}) exceeded`), { code: "max_steps" });
    }
    iterations += 1;
    // Full live history every iteration (base + this turn's user
    // message + the loop's own assistant/tool entries). The store is
    // the single source — no cached slices to desync (a stale slice
    // once dropped the current prompt entirely: provider 400).
    // Mapped for the resolved flavor (chat messages vs Responses
    // input items) — providerStream only transports.
    const rawHistory = store.historyForRun(session);
    const messages =
      resolved.flavor === "responses"
        ? historyToResponsesInput(rawHistory)
        : historyToProviderMessages(rawHistory);
    let stepText = "";
    let stepCalls = [];
    let stepUsage = null;
    const stepResult = await providerStream({
      baseURL: resolved.baseURL,
      key: resolved.key,
      provider: model.provider,
      modelId: model.model_id,
      flavor: resolved.flavor,
      sessionId: session.id,
      messages,
      defs: all,
      signal,
      onToken: (t) => emit({ event: "token", text: t }),
      onReasoning: (t) => emit({ event: "reasoning", text: t }),
      maxRetries: body.max_retries,
      logPrefix: `sweave-engine:${session.id}`,
    });
    stepText = stepResult.text;
    stepCalls = stepResult.calls;
    stepUsage = stepResult.usage;
    const inTok = stepUsage?.prompt_tokens || 0;
    const outTok = stepUsage?.completion_tokens || 0;
    const reasonTok = stepUsage?.completion_tokens_details?.reasoning_tokens || 0;
    // Cache telemetry (real numbers, not the hardcoded zeros the
    // terminal anchor used to emit): prompt_tokens_details is the
    // normalized shape on both flavors (missing = 0, honest).
    const cacheReadTok = stepUsage?.prompt_tokens_details?.cached_tokens || 0;
    totalIn += inTok;
    if (inTok > maxIn) maxIn = inTok;
    totalOut += outTok;
    totalReason += reasonTok;
    totalCacheRead += cacheReadTok;
    finalText = stepText;

    if (stepCalls.length === 0) {
      emit({
        event: "step.boundary",
        reason: "done",
        cost: 0,
        tokens: { input: inTok, output: outTok, reasoning: reasonTok, cache: { read: stepUsage?.prompt_tokens_details?.cached_tokens || 0, write: 0 } },
      });
      store.append(session, {
        id: newMessageId("msg"),
        role: "assistant",
        content: stepText,
        model: `${model.provider}/${model.model_id}`,
        at: Date.now(),
      });
      break;
    }

    const assistantEntry = {
      id: newMessageId("msg"),
      role: "assistant",
      content: stepText,
      toolCalls: stepCalls.map((c) => ({ id: c.id, name: c.name, args: c.args })),
      model: `${model.provider}/${model.model_id}`,
      at: Date.now(),
    };
    store.append(session, assistantEntry);

    let iterSuccess = 0;
    for (const call of stepCalls) {
      if (isAborted()) throw Object.assign(new Error("aborted"), { code: "aborted" });
      const callId = call.id || newCallId();
      toolCallCount += 1;
      const isSweaveKnown = SWEAVE_TOOL_NAMES.has(call.name);
      // Offered-set gate (structural, per role): a tool the loop did
      // not offer does not exist for this turn — specialists calling
      // defer/list/ask_human land here, never at the API.
      const offered = isSweaveKnown ? offeredSweave.has(call.name) : execNames.has(call.name);
      const isSweave = isSweaveKnown && offered;
      if (!offered) {
        const errText = `rejected: unknown tool ${JSON.stringify(call.name)}`;
        emit({ event: "tool.started", callID: callId, tool: call.name, state: { status: "pending", input: call.args } });
        emit({ event: "tool.failed", callID: callId, tool: call.name, state: { status: "error", input: call.args, error: errText } });
        store.append(session, { id: newMessageId("msg"), role: "tool", toolCallId: callId, name: call.name, content: errText, at: Date.now() });
        continue;
      }
      const repeatKey = `${call.name}:${JSON.stringify(call.args || {})}`;
      if (repeatKey === lastRepeat.key) {
        lastRepeat.count += 1;
      } else {
        lastRepeat = { key: repeatKey, count: 1 };
      }
      if (lastRepeat.count >= DOOM_REPEATS && !isSweave) {
        const errText = "rejected: doom_loop suspected (identical call 3x) — try a different approach";
        emit({ event: "tool.started", callID: callId, tool: call.name, state: { status: "pending", input: call.args } });
        emit({ event: "tool.failed", callID: callId, tool: call.name, state: { status: "error", input: call.args, error: errText } });
        store.append(session, { id: newMessageId("msg"), role: "tool", toolCallId: callId, name: call.name, content: errText, at: Date.now() });
        continue;
      }

      if (isSweave) {
        emit({ event: "tool.started", callID: callId, tool: call.name, state: { status: "pending", input: call.args } });
        const result = await callSweaveTool(call.name, call.args, { ...sweaveCtx, session });
        const text = result.text;
        if (result.ok) {
          iterSuccess += 1;
          emit({ event: "tool.completed", callID: callId, tool: call.name, state: { status: "completed", input: call.args, output: text } });
        } else {
          emit({ event: "tool.failed", callID: callId, tool: call.name, state: { status: "error", input: call.args, error: text } });
        }
        store.append(session, { id: newMessageId("msg"), role: "tool", toolCallId: callId, name: call.name, content: text, at: Date.now() });
        continue;
      }

      // Execution tool: permission gate first.
      const key = permissionKey(call.name);
      const { target, isPath } = matchTarget(call.name, call.args);
      const absPath = isPath && target ? resolvePath(cwd, target) : null;
      const gate = gateToolCall(body.permission_map || {}, key, target, { isPath, cwd, absPath });
      emit({ event: "tool.started", callID: callId, tool: call.name, state: { status: "pending", input: call.args } });
      if (gate.verdict === "deny") {
        const errText = `permission denied: ${gate.permission} for ${gate.patterns.join(", ")}`;
        emit({ event: "tool.failed", callID: callId, tool: call.name, state: { status: "error", input: call.args, error: errText } });
        store.append(session, { id: newMessageId("msg"), role: "tool", toolCallId: callId, name: call.name, content: errText, at: Date.now() });
        continue;
      }
      if (gate.verdict === "ask") {
        const answer = mapPermissionResponse(await resolveAsk({ ...execCtxBase }, gate, call.name, callId, call.args || {}));
        if (answer === "reject") {
          const errText = `permission rejected: ${gate.permission} for ${gate.patterns.join(", ")}`;
          emit({ event: "tool.failed", callID: callId, tool: call.name, state: { status: "error", input: call.args, error: errText } });
          store.append(session, { id: newMessageId("msg"), role: "tool", toolCallId: callId, name: call.name, content: errText, at: Date.now() });
          continue;
        }
      }
      const runExec = () => executeTool(call.name, call.args || {}, { cwd, session, signal });
      // No blind work: an abort during a tool settles the turn now —
      // the tool's own signal handling (bash kill) stops the work.
      const settled = await Promise.race([
        withTimeout(runExec(), PER_TOOL_BUDGET_MS, () => ({
          ok: false,
          error: `tool budget exceeded (${PER_TOOL_BUDGET_MS}ms) — partial output kept`,
          partial: "",
        })),
        abortThrow(signal),
      ]);
      if (settled.ok) {
        iterSuccess += 1;
        if (isPath && target) noteFile(absPath || target);
        const out = settled.output || "";
        emit({ event: "tool.completed", callID: callId, tool: call.name, state: { status: "completed", input: call.args, output: out } });
        store.append(session, { id: newMessageId("msg"), role: "tool", toolCallId: callId, name: call.name, content: out || "(no output)", at: Date.now() });
      } else {
        if (isPath && target) noteFile(absPath || target);
        const errText = settled.partial ? `${settled.error}\nPartial output:\n${settled.partial}` : settled.error;
        emit({ event: "tool.failed", callID: callId, tool: call.name, state: { status: "error", input: call.args, error: errText } });
        store.append(session, { id: newMessageId("msg"), role: "tool", toolCallId: callId, name: call.name, content: errText, at: Date.now() });
      }
    }
    emit({
      event: "step.boundary",
      reason: "tool",
      cost: 0,
      tokens: { input: inTok, output: outTok, reasoning: reasonTok, cache: { read: stepUsage?.prompt_tokens_details?.cached_tokens || 0, write: 0 } },
    });
    // Stuckness trip: iterations that executed tools with zero
    // successes lengthen the streak; any success resets it.
    // (Thinking-only iterations exit via the done branch above, so
    // everything reaching here ran at least one call.) Trips loud
    // with a handoff, like the ceiling — never a silent stall.
    noProgressStreak = iterSuccess > 0 ? 0 : noProgressStreak + 1;
    if (noProgressStreak >= NO_PROGRESS_LIMIT) {
      emit(handoffPayload("no_progress"));
      throw Object.assign(
        new Error(`no progress after ${NO_PROGRESS_LIMIT} tool iterations (every executed call failed) — partial work is kept; re-dispatch with narrower scope`),
        { code: "no_progress" },
      );
    }
  }

  return {
    output: finalText,
    usage: { input: totalIn, output: totalOut, reasoning: totalReason, cache_read: totalCacheRead, cache_write: 0, context_input: maxIn },
  };
}
