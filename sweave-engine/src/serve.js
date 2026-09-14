// sweave-engine sidecar: zero-dependency Node HTTP server speaking the
// versioned engine protocol (frozen in ../sweave/engine/protocol.py).
// The Python orchestrator keeps ALL knowledge; this process owns
// execution only (LLM call, token stream, tool execution in cwd).
//
// Step-1 scope: chat path, no tools. Sessions persist to disk
// (restarts don't drop them). Every response carries
// X-Sweave-Engine-Protocol.

import { createServer } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";
import { withProviderRetry, providerHttpError, DEFAULT_MAX_RETRIES } from "./retry.js";
import { SessionStore, newMessageId } from "./sessions.js";
import { resolveProvider, KNOWN_TOOLS, TOOL_BASELINE, SWEAVE_NATIVE_TOOLS, ENGINE_USER_AGENT, SESSION_HEADER } from "./providers.js";
import {
  historyToResponsesInput,
  providerResponsesStream,
} from "./responses.js";
import { extractReasoningDelta, historyToProviderMessages, needsLoop, runLoop } from "./loop.js";

const PROTOCOL_VERSION = process.env.SWEAVE_ENGINE_PROTOCOL_VERSION || "3";
const VERSION_HEADER = "X-Sweave-Engine-Protocol";

const args = process.argv.slice(2);
function arg(name, fallback) {
  const i = args.indexOf(name);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
}
const DATA_DIR =
  arg("--data-dir", null) ||
  process.env.SWEAVE_ENGINE_DATA_DIR ||
  join(homedir(), ".sweave", "engine");
const store = new SessionStore(DATA_DIR);
// One live turn per engine session (busy-guard parity with opencode's
// assertNotBusy + our TurnActiveError): sessionId -> { controller, done }.
const live = new Map();

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    "Content-Type": "application/json",
    [VERSION_HEADER]: PROTOCOL_VERSION,
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (c) => {
      raw += c;
      if (raw.length > 4 * 1024 * 1024) reject(new Error("body too large"));
    });
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        reject(new Error("bad JSON body"));
      }
    });
    req.on("error", reject);
  });
}

function validateRun(body) {
  if (!body || typeof body !== "object") return "bad:body (must be an object)";
  for (const f of ["session_id", "composed_prompt", "tools", "permission_map", "model", "turn_timeout", "cwd"]) {
    if (body[f] === undefined || body[f] === null) return `missing:${f}`;
  }
  if (!Array.isArray(body.tools) || !body.tools.every((t) => typeof t === "string")) {
    return "bad:tools (must be a list of tool names)";
  }
  const unknown = body.tools.filter((t) => !KNOWN_TOOLS.has(t));
  if (unknown.length > 0) return `bad:tools (unknown: ${[...new Set(unknown)].sort().join(",")})`;
  if (typeof body.permission_map !== "object") return "bad:permission_map (must be an object)";
  if (!body.model || typeof body.model !== "object") return "bad:model (must be a ModelRef object)";
  if (!body.model.provider) return "bad:model (engine needs a resolved provider)";
  if (!body.model.model_id) return "bad:model (engine needs a model_id)";
  if (typeof body.turn_timeout !== "number" || !(body.turn_timeout > 0)) {
    return "bad:turn_timeout (must be > 0 seconds)";
  }
  // Retry budget (additive): retries AFTER the first provider attempt
  // (default 3). Absent keeps the default; the orchestrator sends its
  // configured value per turn.
  if (body.max_retries !== undefined && (typeof body.max_retries !== "number" || !(body.max_retries >= 0))) {
    return "bad:max_retries (must be >= 0 when present)";
  }
  // Step-2 additions (optional, additive — absence keeps step-1 behavior):
  // delegation_id links sweave-tool calls (defer/escalate/ask) to the
  // owning delegation; role ("orchestrator"|"specialist", default
  // specialist = least privilege) gates which sweave tools are offered.
  if (body.delegation_id !== undefined && typeof body.delegation_id !== "string") {
    return "bad:delegation_id (must be a string when present)";
  }
  if (body.role !== undefined && body.role !== "orchestrator" && body.role !== "specialist") {
    return "bad:role (must be orchestrator|specialist when present)";
  }
  return null;
}

function approxTokens(text) {
  if (!text) return 0;
  return Math.max(1, Math.floor(text.split(/\s+/).length * 4 / 3));
}

function sseEvent(res, obj) {
  res.write(`data: ${JSON.stringify(obj)}\n\n`);
}

async function runLoopTurn(sessionId, session, body, res, turn, finish, timer, userMessageId) {
  const emit = (obj) => {
    try {
      sseEvent(res, obj);
    } catch {
      // Client gone mid-turn; the catch below finishes silently.
    }
  };
  try {
    const resolved = resolveProvider(body.model.provider, body.model.model_id);
    if (!resolved.ok) {
      // Same named turn-start gate as the single-shot path: a failed
      // resolution (auth_missing, pending-transport flavor) surfaces
      // here, never cryptically inside the first loop fetch.
      emit({ event: "error", code: resolved.code, message: resolved.reason, provider: body.model.provider });
      clearTimeout(timer);
      finish();
      try {
        res.end();
      } catch {}
      return;
    }
    const { output, usage } = await runLoop({
      session,
      saveSession: () => store.save(),
      store,
      emit,
      body,
      resolved,
      cwd: body.cwd || ".",
      signal: turn.controller.signal,
      isAborted: () => turn.finished,
    });
    clearTimeout(timer);
    if (turn.finished) return; // timeout/abort path already answered
    finish();
    // user_message_id (protocol v3): lets the orchestrator name this
    // turn's prompt in a later /revert (edit = history rewrite, never
    // a session rotation).
    emit({ event: "done", output, user_message_id: userMessageId || null, model_used: { provider: body.model.provider, model_id: body.model.model_id } });
    const hasUsage = usage && (usage.input > 0 || usage.output > 0);
    emit({
      event: "tokens_used",
      input: usage.input,
      output: usage.output,
      reasoning: usage.reasoning,
      cache_read: usage.cache_read || 0,
      cache_write: usage.cache_write || 0,
      cost: 0,
      ...(hasUsage ? {} : { estimated: true }),
    });
    try {
      res.end();
    } catch {}
  } catch (err) {
    clearTimeout(timer);
    if (turn.finished) return;
    finish();
    const code = (err && err.code) || "provider_error";
    store.append(session, {
      id: newMessageId("msg"),
      role: "assistant",
      content: "",
      failed: true,
      error: code === "aborted" ? "turn aborted" : String((err && err.message) || err),
      at: Date.now(),
    });
    emit({ event: "error", code, message: String((err && err.message) || err).slice(0, 500) });
    try {
      res.end();
    } catch {}
  }
}

async function runTurn(sessionId, body, res) {
  const session = store.ensure(sessionId);
  const model = body.model;
  const turnTimeoutMs = Math.max(1, body.turn_timeout) * 1000;

  const resolved = resolveProvider(model.provider, model.model_id);
  if (!resolved.ok) {
    // Named turn-start failure (auth_missing) — loud, before any token.
    sseEvent(res, { event: "error", code: resolved.code, message: resolved.reason, provider: model.provider });
    res.end();
    return;
  }

  const userMsg = {
    id: newMessageId("msg"),
    role: "user",
    content: body.composed_prompt,
    at: Date.now(),
  };
  store.append(session, userMsg);

  const controller = new AbortController();
  const turn = { controller, finished: false };
  live.set(sessionId, turn);

  const finish = () => {
    turn.finished = true;
    if (live.get(sessionId) === turn) live.delete(sessionId);
  };

  let timer = null;
  const onTimeout = () => {
    if (turn.finished) return;
    controller.abort(new Error("turn_timeout"));
    sseEvent(res, {
      event: "error",
      code: "turn_timeout",
      message: `turn_timeout_exceeded_${body.turn_timeout}s`,
    });
    try {
      res.end();
    } catch {}
    finish();
  };
  timer = setTimeout(onTimeout, turnTimeoutMs);

  // Step-2 agentic loop (execution and/or orchestrator sweave tools
  // requested). The legacy single-shot chat path below stays
  // byte-identical for tool-less non-orchestrator turns.
  if (needsLoop(body)) {
    await runLoopTurn(sessionId, session, body, res, turn, finish, timer, userMsg.id);
    return;
  }

  const history = store
    .historyForRun(session)
    .filter((m) => m.id !== userMsg.id) // appended above; re-add below in order
    .map((m) => ({
      role: m.role === "assistant" ? "assistant" : "user",
      content: m.failed ? `[previous error: ${m.error || "unknown"}]` : m.content,
    }));
  history.push({ role: "user", content: body.composed_prompt });

  const assistantId = newMessageId("msg");
  let output = "";
  let usage = null;
  if (resolved.flavor === "responses") {
    // Responses flavor: same downstream events, different wire. The
    // small tail below mirrors the chat path's (append + done +
    // tokens) deliberately — restructuring the chat try/catch to
    // share it risks the timeout/abort semantics; duplication is
    // the honest trade.
    const entries = store
      .historyForRun(session)
      .filter((m) => m.id !== userMsg.id);
    entries.push({ role: "user", content: body.composed_prompt });
    try {
      const maxRetries = body.max_retries ?? DEFAULT_MAX_RETRIES;
      const step = await withProviderRetry(() => providerResponsesStream({
        baseURL: resolved.baseURL,
        key: resolved.key,
        modelId: body.model.model_id,
        sessionId,
        input: historyToResponsesInput(entries),
        defs: [],
        signal: controller.signal,
        onToken: (t) => {
          // Forward-only: the attempt's full text comes back as
          // step.text. Accumulating here would duplicate the prefix
          // across retries (each attempt replays from zero).
          sseEvent(res, { event: "token", text: t });
        },
        onReasoning: (t) => {
          sseEvent(res, { event: "reasoning", text: t });
        },
      }), {
        maxRetries,
        signal: controller.signal,
        onRetry: ({ attempt, waitMs, error }) => {
          try {
            process.stderr.write(
              `sweave-engine:${sessionId}: provider retry ${attempt} in ${waitMs}ms (${String((error && error.message) || error).slice(0, 160)})\n`
            );
          } catch {}
        },
      });
      output = step.text;
      usage = step.usage;
    } catch (err) {
      // Terminal provider failure on the responses flavor: end the
      // turn loudly HERE (failed record + error SSE + res.end),
      // never re-throw past the already-committed SSE headers — a
      // throw lands in the request catch whose sendJson(500) cannot
      // run after headers, leaving the client blocked until the
      // turn timeout (the 2026-09-14 zen-suite hang: ~30 min of
      // silence on a provider 404). Mirrors the chat branch below.
      clearTimeout(timer);
      if (turn.finished) return; // timeout/abort path already answered
      finish();
      const failed = err && err.message === "turn_timeout";
      const text = `provider ${String((err && err.message) || err).slice(0, 300)}`;
      store.append(session, {
        id: assistantId,
        role: "assistant",
        content: "",
        failed: true,
        error: failed ? `turn_timeout_exceeded_${body.turn_timeout}s` : text,
        at: Date.now(),
      });
      if (!(err && err.name === "AbortError") && !failed) {
        sseEvent(res, { event: "error", code: "provider_error", message: text });
      }
      try {
        res.end();
      } catch {}
      return;
    }
    clearTimeout(timer);
    if (turn.finished) return; // timeout/abort path already answered
    finish();
    store.append(session, {
      id: assistantId,
      role: "assistant",
      content: output,
      model: `${model.provider}/${model.model_id}`,
      at: Date.now(),
    });
    sseEvent(res, {
      event: "done",
      output,
      message_id: assistantId,
      user_message_id: userMsg.id,
      model_used: { provider: model.provider, model_id: model.model_id },
    });
    sseEvent(res, {
      event: "tokens_used",
      ...(usage
        ? {
            input: usage.prompt_tokens || 0,
            output: usage.completion_tokens || 0,
            reasoning: usage.completion_tokens_details?.reasoning_tokens || 0,
            cache_read: usage.prompt_tokens_details?.cached_tokens || 0,
            cache_write: 0,
            cost: 0,
          }
        : {
            input: 0,
            output: approxTokens(output),
            reasoning: 0,
            cache_read: 0,
            cache_write: 0,
            cost: 0,
            estimated: true,
          }),
    });
    res.end();
  } else {
  try {
    // Pre-stream retry: 429/5xx/network failures at request time wait
    // and retry inside the turn (same history, no rotation). A cut
    // MID-stream stays terminal — loud, session kept, and a user retry
    // continues the same session (the no-rotation backstop).
    const maxRetries = body.max_retries ?? DEFAULT_MAX_RETRIES;
    const upstream = await withProviderRetry(async () => {
      const resp = await fetch(`${resolved.baseURL}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${resolved.key || "no-key"}`,
          "User-Agent": ENGINE_USER_AGENT,
          [SESSION_HEADER]: sessionId,
          ...(model.provider === "openrouter"
            ? { "HTTP-Referer": "https://github.com/sweave", "X-Title": "Sweave Engine" }
            : {}),
        },
        body: JSON.stringify({
          model: body.model.model_id,
          messages: history,
          stream: true,
          stream_options: { include_usage: true },
        }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        const text = await resp.text().catch(() => "");
        throw providerHttpError(resp.status, resp.headers, text.slice(0, 300));
      }
      return resp;
    }, {
      maxRetries,
      signal: controller.signal,
      onRetry: ({ attempt, waitMs, error }) => {
        try {
          process.stderr.write(
            `sweave-engine:${sessionId}: provider retry ${attempt} in ${waitMs}ms (${String((error && error.message) || error).slice(0, 160)})\n`
          );
        } catch {}
      },
    });
    const reader = upstream.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let closed = false;
    const pump = async () => {
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
          const delta = obj?.choices?.[0]?.delta?.content || "";
          if (delta) {
            output += delta;
            sseEvent(res, { event: "token", text: delta });
          }
          // Thinking capture (single-shot chat path mirrors the
          // loop's providerStream): reasoning never joins output.
          const rdelta = extractReasoningDelta(obj?.choices?.[0]?.delta);
          if (rdelta) sseEvent(res, { event: "reasoning", text: rdelta });
          if (obj?.usage) usage = obj.usage;
        }
        if (turn.finished) {
          try {
            await reader.cancel();
          } catch {}
          closed = true;
          break;
        }
      }
      return closed;
    };
    const abortedRemotely = await pump();
    clearTimeout(timer);
    if (turn.finished) return; // timeout/abort path already answered
    finish();
    const assistantMsg = {
      id: assistantId,
      role: "assistant",
      content: output,
      model: `${model.provider}/${model.model_id}`,
      at: Date.now(),
    };
    store.append(session, assistantMsg);
    sseEvent(res, {
      event: "done",
      output,
      message_id: assistantId,
      user_message_id: userMsg.id,
      model_used: { provider: model.provider, model_id: model.model_id },
    });
    // tokens_used terminal — identical shape to the M1.9 audit anchor.
    // cost is 0 until a pricing table lands (tokens are real).
    let tokens;
    if (usage) {
      tokens = {
        input: usage.prompt_tokens || 0,
        output: usage.completion_tokens || 0,
        reasoning: usage.completion_tokens_details?.reasoning_tokens || 0,
        cache_read: usage.prompt_tokens_details?.cached_tokens || 0,
        cache_write: 0,
        cost: 0,
      };
    } else {
      tokens = {
        input: 0,
        output: approxTokens(output),
        reasoning: 0,
        cache_read: 0,
        cache_write: 0,
        cost: 0,
        estimated: true,
      };
    }
    sseEvent(res, { event: "tokens_used", ...tokens });
    res.end();
    void abortedRemotely;
  } catch (err) {
    clearTimeout(timer);
    if (turn.finished) return;
    finish();
    const failed = err && err.message === "turn_timeout";
    store.append(session, {
      id: assistantId,
      role: "assistant",
      content: "",
      failed: true,
      error: failed ? `turn_timeout_exceeded_${body.turn_timeout}s` : String((err && err.message) || err),
      at: Date.now(),
    });
    if (err && err.name === "AbortError" && !failed) {
      sseEvent(res, { event: "error", code: "aborted", message: "turn aborted" });
    } else if (!failed) {
      sseEvent(res, { event: "error", code: "provider_error", message: String((err && err.message) || err).slice(0, 500) });
    }
    try {
      res.end();
    } catch {}
  }
  } // end else (chat flavor) — the responses branch above is self-contained
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", "http://localhost");
    if (req.method === "GET" && url.pathname === "/health") {
      return sendJson(res, 200, {
        protocol_version: PROTOCOL_VERSION,
        name: "sweave-engine",
        tools: TOOL_BASELINE,
        sweave_tools: SWEAVE_NATIVE_TOOLS,
      });
    }
    if (req.method === "POST" && url.pathname === "/run") {
      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: "bad_request", reason: String((e && e.message) || e) });
      }
      const problem = validateRun(body);
      if (problem) return sendJson(res, 400, { error: "bad_request", reason: problem });
      if (live.has(body.session_id)) {
        return sendJson(res, 409, { error: "turn_active", reason: "session already has a live turn" });
      }
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        [VERSION_HEADER]: PROTOCOL_VERSION,
      });
      req.on("close", () => {
        const turn = live.get(body.session_id);
        if (turn && !turn.finished) turn.controller.abort(new Error("client-closed"));
      });
      await runTurn(body.session_id, body, res);
      return;
    }
    if (req.method === "POST" && url.pathname === "/abort") {
      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: "bad_request", reason: String((e && e.message) || e) });
      }
      if (!body.session_id) return sendJson(res, 400, { error: "bad_request", reason: "missing:session_id" });
      const turn = live.get(body.session_id);
      if (!turn || turn.finished) {
        return sendJson(res, 409, { error: "no_live_turn", reason: "session has no live turn" });
      }
      turn.controller.abort(new Error("user-abort"));
      const deadline = Date.now() + 2000;
      while (!turn.finished && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 50));
      }
      if (turn.finished) return sendJson(res, 200, { outcome: "acknowledged", session_id: body.session_id });
      // Stop was attempted but the turn may still run provider-side.
      return sendJson(res, 200, { outcome: "UNCONFIRMED", session_id: body.session_id });
    }
    if (req.method === "POST" && url.pathname === "/revert") {
      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: "bad_request", reason: String((e && e.message) || e) });
      }
      if (!body.session_id) return sendJson(res, 400, { error: "bad_request", reason: "missing:session_id" });
      // Rewrite mode (no-rotation invariant): `before_message` drops
      // the named message itself too — the old prompt must not survive
      // alongside its replacement. Plain `to_message` keeps opencode
      // pointer semantics (keep named, drop after).
      const target = body.before_message || body.to_message;
      if (!target) return sendJson(res, 400, { error: "bad_request", reason: "missing:to_message" });
      const session = store.get(body.session_id);
      if (!session) return sendJson(res, 404, { error: "unknown_session", reason: body.session_id });
      if (live.has(body.session_id)) {
        return sendJson(res, 409, { error: "turn_active", reason: "cannot revert mid-turn" });
      }
      if (!session.messages.some((m) => m.id === target)) {
        return sendJson(res, 400, { error: "unknown_message", reason: target });
      }
      session.revert = { to_message: target, exclusive: Boolean(body.before_message), at: Date.now() };
      store.save();
      return sendJson(res, 200, { ok: true, to_message: target });
    }
    return sendJson(res, 404, { error: "not_found", reason: url.pathname });
  } catch (e) {
    try {
      sendJson(res, 500, { error: "internal", reason: String((e && e.message) || e) });
    } catch {}
  }
});

const listenPort = parseInt(arg("--port", "0"), 10) || 0;
server.listen(listenPort, "127.0.0.1", () => {
  const addr = server.address();
  const port = typeof addr === "object" && addr ? addr.port : listenPort;
  // Port discovery line on stdout (logs go to stderr) — mirrors the
  // opencode log-file discovery pattern, minus the log scraping.
  process.stdout.write(`SWEAVE_ENGINE_PORT=${port}\n`);
});
server.on("error", (e) => {
  process.stderr.write(`sweave-engine: listen failed: ${(e && e.message) || e}\n`);
  process.exit(1);
});
