// Responses-API transport (OpenAI Responses shape) for gateway models
// on the `responses` flavor (Go/Zen docs tables). Same contract as
// the chat path: the caller gets { text, calls: [{id, name, args}],
// usage } and everything downstream (gates, execution, trace events)
// stays transport-agnostic.
//
// Wire (per the Responses API): POST /responses {model, input, stream,
// tools?} → SSE response.output_text.delta /
// response.function_call_arguments.delta / response.output_item.done /
// response.completed|failed. History maps to input items (message /
// function_call / function_call_output); unknown item shapes are
// dropped, never sent.

import { ENGINE_USER_AGENT, SESSION_HEADER, reasoningEffortFor, shouldStripReasoningFeatures } from "./providers.js";
import { sanitizeHistory, capHistory, historyTruncationNote } from "./sessions.js";

export function historyToResponsesInput(entries) {
  // Cap FIRST, sanitize SECOND (sessions.js ordering rule): capping
  // a sanitized history severs validated pairs into replay poison.
  const capped = capHistory(entries || []);
  const clean = sanitizeHistory(capped.entries);
  const omitted = (entries || []).length - clean.length;
  const out = [];
  if (omitted > 0) {
    out.push({ role: "user", content: historyTruncationNote(omitted, capped.droppedChars, capped.droppedTurns) });
  }
  for (const m of clean) {
    if (m.role === "user") {
      out.push({ role: "user", content: m.content || "" });
    } else if (m.role === "assistant" && !m.failed) {
      // Thinking replay (agent parity): the model re-reads its own
      // prior reasoning as native `reasoning` input items (summary
      // text only — never encrypted payloads, which we never store).
      // Emission order mirrors generation: thinking, then text,
      // then the calls it produced. A gateway that rejects the
      // shape fails the turn LOUDLY on first contact (never a silent
      // drop); providerResponsesStream strips-and-retries once with
      // a session-tagged stderr line, so an incompatible gateway
      // degrades to thinking-less turns instead of bricking the
      // session. Chat flavor deliberately does NOT replay thinking
      // (no standard field; strict gateways 400 unknown keys — the
      // _sweave_managed lesson) — journal persistence still serves
      // the transcript there.
      if (typeof m.reasoning === "string" && m.reasoning) {
        out.push({
          type: "reasoning",
          summary: [{ type: "summary_text", text: m.reasoning }],
        });
      }
      if (m.toolCalls && m.toolCalls.length > 0) {
        if (m.content) out.push({ role: "assistant", content: m.content });
        for (const tc of m.toolCalls) {
          out.push({
            type: "function_call",
            call_id: tc.id || null,
            name: tc.name,
            arguments: JSON.stringify(tc.args || {}),
          });
        }
      } else {
        out.push({ role: "assistant", content: m.content || "" });
      }
    } else if (m.role === "tool") {
      out.push({
        type: "function_call_output",
        call_id: m.toolCallId || null,
        output: m.content || "",
      });
    }
    // failed assistant entries stay out (same rule as the chat path).
  }
  return out;
}

export function responsesToolDefs(defs) {
  return (defs || []).map((d) => ({
    type: "function",
    name: d.name,
    description: d.description || "",
    parameters: d.parameters || { type: "object", properties: {} },
  }));
}

export async function providerResponsesStream({
  baseURL,
  key,
  modelId,
  modelVariant,
  sessionId,
  input,
  defs,
  signal,
  onToken,
  onReasoning,
}) {
  // Reasoning-effort request from the model's +variant suffix (agent
  // parity with the opencode harness, which sends variant natively).
  // `none` drops the reasoning key (thinking not requested);
  // otherwise effort rides verbatim next to the summary unlock.
  const effortReq = reasoningEffortFor(modelVariant);
  const reasoningParam = (strip) => {
    if (strip) return undefined;
    if (effortReq && effortReq.none) return undefined;
    if (effortReq) return { summary: "auto", effort: effortReq.effort };
    return { summary: "auto" };
  };
  const attempt = (clientInput, stripReasoning) =>
    providerResponsesAttempt({
      baseURL,
      key,
      modelId,
      sessionId,
      input: clientInput,
      defs,
      signal,
      onToken,
      onReasoning,
      reasoning: reasoningParam(stripReasoning),
    });
  try {
    return await attempt(input, false);
  } catch (err) {
    // Compat fallback: a gateway rejecting replayed `reasoning`
    // input items OR an unknown `effort` value 400s here. Strip both
    // and retry ONCE within the same turn — an incompatible gateway
    // degrades to default-effort thinking-less turns (logged,
    // session-tagged) instead of bricking every turn on the
    // session. Only fires when the error names reasoning/effort and
    // the attempt actually sent such features; everything rethrows.
    const carriesReasoning =
      Array.isArray(input) &&
      input.some((i) => i && i.type === "reasoning");
    const sentFeatures = carriesReasoning || Boolean(effortReq && !effortReq.none);
    if (!shouldStripReasoningFeatures(err, sentFeatures)) {
      throw err;
    }
    try {
      process.stderr.write(
        `sweave-engine:${sessionId || "unknown"}: reasoning features rejected (400); ` +
          `retrying default-effort thinking-less once\n`
      );
    } catch {}
    const stripped = Array.isArray(input)
      ? input.filter((i) => !(i && i.type === "reasoning"))
      : input;
    const out = await attempt(stripped, true);
    out.reasoningCompatRetry = true;
    return out;
  }
}

async function providerResponsesAttempt({
  baseURL,
  key,
  modelId,
  sessionId,
  input,
  defs,
  signal,
  onToken,
  onReasoning,
  reasoning,
}) {
  const resp = await fetch(`${baseURL}/responses`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key || "no-key"}`,
      "User-Agent": ENGINE_USER_AGENT,
      [SESSION_HEADER]: sessionId || "unknown",
    },
    body: JSON.stringify({
      model: modelId,
      input,
      stream: true,
      // Reasoning summary (opencode parity, public source:
      // sst/opencode transform.ts sends reasoningSummary:"auto" for
      // every opencode-family model). Without it the gateway never
      // opens the reasoning channel and inlines thinking into
      // output_text (observed live on muse-spark-contributor: 7.5k
      // reasoning tokens, zero reasoning events, thinking fragments
      // leading the persisted reply). The caller's `reasoning`
      // object carries summary + optional +variant effort (absent
      // for thinking-off `none` and on the compat retry).
      ...(reasoning ? { reasoning } : {}),
      ...(defs && defs.length > 0 ? { tools: responsesToolDefs(defs) } : {}),
    }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    const err = new Error(`provider ${resp.status}: ${text.slice(0, 300)}`);
    err.status = resp.status;
    try {
      err.headers = resp.headers;
    } catch {}
    err.body = text.slice(0, 500);
    throw err;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let text = "";
  const argDeltas = new Map(); // item_id -> concatenated arguments
  const calls = [];
  let usage = null;
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
        const type = obj && obj.type;
        if (type === "response.output_text.delta") {
          const delta = obj.delta || "";
          if (delta) {
            text += delta;
            if (onToken) {
              try {
                onToken(delta);
              } catch {}
            }
          }
        } else if (type === "response.function_call_arguments.delta") {
          const id = obj.item_id || "";
          argDeltas.set(id, (argDeltas.get(id) || "") + (obj.delta || ""));
        } else if (
          type === "response.reasoning_summary_text.delta" ||
          type === "response.reasoning_text.delta" ||
          type === "response.reasoning.delta" ||
          type === "response.reasoning_summary.delta"
        ) {
          // Thinking capture (official carrier +
          // compat-server variants): reasoning summaries stream
          // here, never as output_text. Forwarded, never output.
          const rdelta = typeof obj.delta === "string" ? obj.delta : "";
          if (rdelta && onReasoning) {
            try {
              onReasoning(rdelta);
            } catch {}
          }
        } else if (type === "response.output_item.done") {
          const item = obj.item || {};
          if (item.type === "function_call") {
            const raw =
              typeof item.arguments === "string" && item.arguments
                ? item.arguments
                : argDeltas.get(item.id) || "{}";
            let args = {};
            try {
              args = JSON.parse(raw);
            } catch {
              args = { _raw: raw };
            }
            calls.push({
              id: item.call_id || item.id,
              name: item.name,
              args,
            });
          }
        } else if (type === "response.completed") {
          const u = (obj.response && obj.response.usage) || obj.usage || null;
          if (u) {
            usage = {
              prompt_tokens: u.input_tokens || 0,
              completion_tokens: u.output_tokens || 0,
              completion_tokens_details: {
                reasoning_tokens:
                  (u.output_tokens_details && u.output_tokens_details.reasoning_tokens) || 0,
              },
              prompt_tokens_details: {
                cached_tokens:
                  (u.input_tokens_details && u.input_tokens_details.cached_tokens) || 0,
              },
            };
          }
        } else if (type === "response.failed" || type === "error") {
          const msg =
            ((obj.response && obj.response.error) || obj.error || obj.message || "unknown");
          throw new Error(
            `provider responses-failed: ${String(msg && msg.message ? msg.message : msg).slice(0, 300)}`
          );
        }
      }
    }
  };
  await pump();
  return { text, calls, usage };
}
