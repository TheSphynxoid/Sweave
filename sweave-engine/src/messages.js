// Anthropic Messages-API transport for gateway models on the
// `messages` flavor (Go/Zen docs tables: Claude + minimax/qwen
// messages-flavor ids). Same contract as the chat and responses
// paths: the caller gets { text, calls: [{id, name, args}], usage }
// and everything downstream (gates, execution, trace events) stays
// transport-agnostic.
//
// Wire (Anthropic Messages shape, via the gateway): POST /messages
// {model, max_tokens, messages, stream, tools?} → SSE message_start
// / content_block_start|delta|stop / message_delta / message_stop.
// History maps to roles with content BLOCKS (text/tool_use/
// tool_result); unknown shapes are dropped, never sent.
//
// Auth mirrors the other transports (gateway Bearer + validated
// client headers). NO `anthropic-version` header: that belongs to
// the direct Anthropic API, and this transport speaks to the
// gateway, which translates (same reason we send no provider-native
// fields anywhere else). If a live probe proves otherwise, this is
// the documented assumption to revisit.
//
// Deliberate v1 limits (documented, not silent):
// - thinking blocks are DROPPED from mapped history (replaying them
//   needs server-issued signatures we never store; fabricated ones
//   400). Journal persistence still serves the transcript.
// - +variant effort is NOT sent (Anthropic budgets are token counts,
//   not low/high presets — inventing a mapping burns real turns).
// - max_tokens defaults to 32000 (the ecosystem anchor: the
//   pre-marker opencode body carried max_tokens:32000) with a
//   halve-till-4096 strip-retry on max_tokens 400s.

import { ENGINE_USER_AGENT, SESSION_HEADER } from "./providers.js";
import { sanitizeHistory, capHistory, historyTruncationNote } from "./sessions.js";
import { providerHttpError } from "./retry.js";

export const MESSAGES_DEFAULT_MAX_TOKENS = 32000;
export const MESSAGES_MIN_MAX_TOKENS = 4096;

export function historyToMessagesInput(entries) {
  // Cap FIRST, sanitize SECOND (sessions.js ordering rule — same
  // severed-pair poison class as the other flavors). The merge pass
  // below additionally guarantees role alternation no matter what
  // the cap/sanitize drop.
  const capped = capHistory(entries || []);
  const clean = sanitizeHistory(capped.entries);
  const omitted = (entries || []).length - clean.length;
  const out = [];
  // The truncation note is a user message and joins the alternation
  // merge like any other (it is newest, so it never strands).
  const queue = omitted > 0
    ? [
        {
          role: "user",
          content: historyTruncationNote(
            omitted, capped.droppedChars, capped.droppedTurns
          ),
        },
        ...clean,
      ]
    : [...clean];
  const push = (role, content) => {
    if (!Array.isArray(content) || content.length === 0) return;
    const prev = out[out.length - 1];
    // Anthropic requires alternating roles: merge consecutive
    // same-role messages (the cap/sanitize can strand tool outputs
    // or empty prompts into adjacency).
    if (prev && prev.role === role) {
      prev.content.push(...content);
    } else {
      out.push({ role, content });
    }
  };
  for (const m of queue) {
    if (!m || typeof m !== "object") continue;
    if (m.role === "user") {
      if (typeof m.content === "string" && m.content) {
        push("user", [{ type: "text", text: m.content }]);
      }
    } else if (m.role === "assistant" && !m.failed) {
      const blocks = [];
      if (typeof m.content === "string" && m.content) {
        blocks.push({ type: "text", text: m.content });
      }
      if (Array.isArray(m.toolCalls)) {
        for (const tc of m.toolCalls) {
          if (!tc || typeof tc.name !== "string" || !tc.name) continue;
          blocks.push({
            type: "tool_use",
            id: typeof tc.id === "string" && tc.id ? tc.id : undefined,
            name: tc.name,
            input: tc.args && typeof tc.args === "object" ? tc.args : {},
          });
        }
      }
      // Thinking is dropped (see header): a text-less, call-less
      // thinking remnant carries nothing replayable — skip it rather
      // than emitting an empty (invalid) assistant message.
      if (blocks.length > 0) push("assistant", blocks);
    } else if (m.role === "tool") {
      if (m.toolCallId) {
        push("user", [
          {
            type: "tool_result",
            tool_use_id: m.toolCallId,
            content:
              typeof m.content === "string" && m.content
                ? m.content
                : "(no output)",
          },
        ]);
      }
    }
    // failed assistant entries stay out (same rule as every flavor).
  }
  // The API requires a user message first: drop a leading
  // assistant/tool drift (revert slices can strand one).
  while (out.length > 0 && out[0].role !== "user") out.shift();
  return out;
}

export function messagesToolDefs(defs) {
  return (defs || []).map((d) => ({
    name: d.name,
    description: d.description || "",
    input_schema: d.parameters || { type: "object", properties: {} },
  }));
}

function extractAnthropicError(bodyText) {
  // Gateway wraps upstream failures; try the Anthropic error shape
  // first ({type:"error",error:{...}}), fall back to raw text.
  try {
    const obj = JSON.parse(bodyText);
    const err = obj && (obj.error || obj.message);
    if (typeof err === "string" && err) return err;
    if (err && typeof err === "object") {
      const msg = err.message || err.type;
      if (typeof msg === "string" && msg) {
        return err.type && err.type !== msg ? `${err.type}: ${msg}` : msg;
      }
    }
  } catch {}
  return bodyText;
}

export async function providerMessagesStream({
  baseURL,
  key,
  modelId,
  sessionId,
  input,
  defs,
  signal,
  onToken,
  onReasoning,
}) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${key || "no-key"}`,
    "User-Agent": ENGINE_USER_AGENT,
    [SESSION_HEADER]: sessionId || "unknown",
  };
  const invoke = async (maxTokens) => {
    const resp = await fetch(`${baseURL}/messages`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model: modelId,
        max_tokens: maxTokens,
        messages: input,
        stream: true,
        ...(defs && defs.length > 0 ? { tools: messagesToolDefs(defs) } : {}),
      }),
      signal,
    });
    if (!resp.ok || !resp.body) {
      const text = await resp.text().catch(() => "");
      const err = new Error(
        `provider ${resp.status}: ${extractAnthropicError(text).slice(0, 300)}`
      );
      err.status = resp.status;
      try {
        err.headers = resp.headers;
      } catch {}
      err.body = text.slice(0, 500);
      throw err;
    }
    return resp;
  };
  // max_tokens strip-retry (same self-healing philosophy as the
  // reasoning fallbacks): a fixed default above a model's ceiling
  // 400s deterministically. Halve till the floor, once per level,
  // loudly; the floor failing means a real problem, not a guess.
  let maxTokens = MESSAGES_DEFAULT_MAX_TOKENS;
  let resp;
  for (;;) {
    try {
      resp = await invoke(maxTokens);
      break;
    } catch (err) {
      const text = `${(err && err.message) || ""}\n${(err && err.body) || ""}`;
      if (
        err &&
        err.status === 400 &&
        /max_tokens/i.test(text) &&
        maxTokens > MESSAGES_MIN_MAX_TOKENS
      ) {
        maxTokens = Math.max(
          MESSAGES_MIN_MAX_TOKENS,
          Math.floor(maxTokens / 2)
        );
        try {
          process.stderr.write(
            `sweave-engine:${sessionId || "unknown"}: max_tokens rejected (400); ` +
              `retrying with ${maxTokens} once\n`
          );
        } catch {}
        continue;
      }
      throw err;
    }
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let text = "";
  const textBlocks = new Map(); // index -> text
  const toolBlocks = new Map(); // index -> { id, name, json }
  let inputTokens = 0;
  let outputTokens = 0;
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
      if (type === "message_start") {
        const u = (obj.message && obj.message.usage) || null;
        if (u) inputTokens = u.input_tokens || 0;
      } else if (type === "content_block_start") {
        const index = obj.index ?? 0;
        const block = obj.content_block || {};
        if (block.type === "tool_use") {
          toolBlocks.set(index, {
            id: block.id || "",
            name: block.name || "",
            json: "",
          });
        } else {
          textBlocks.set(index, textBlocks.get(index) || "");
        }
      } else if (type === "content_block_delta") {
        const index = obj.index ?? 0;
        const delta = obj.delta || {};
        if (delta.type === "text_delta" && typeof delta.text === "string") {
          textBlocks.set(index, (textBlocks.get(index) || "") + delta.text);
          if (delta.text && onToken) {
            try {
              onToken(delta.text);
            } catch {}
          }
        } else if (
          delta.type === "input_json_delta" &&
          typeof delta.partial_json === "string"
        ) {
          const slot = toolBlocks.get(index) || { id: "", name: "", json: "" };
          slot.json += delta.partial_json;
          toolBlocks.set(index, slot);
        } else if (
          (delta.type === "thinking_delta" || delta.type === "signature_delta") &&
          typeof delta.thinking === "string" &&
          delta.thinking &&
          onReasoning
        ) {
          try {
            onReasoning(delta.thinking);
          } catch {}
        }
      } else if (type === "message_delta") {
        const u = obj.usage || null;
        if (u) outputTokens = u.output_tokens || 0;
      } else if (type === "error") {
        const msg =
          (obj.error && (obj.error.message || obj.error.type)) ||
          obj.message ||
          "unknown";
        throw new Error(
          `provider messages-failed: ${String(msg).slice(0, 300)}`
        );
      }
      // message_stop, content_block_stop, ping: structural, no action.
    }
  }
  const ordered = [...textBlocks.entries()].sort((a, b) => a[0] - b[0]);
  for (const [, t] of ordered) text += t;
  const calls = [...toolBlocks.entries()]
    .sort((a, b) => a[0] - b[0])
    .filter(([, c]) => c.name)
    .map(([, c]) => {
      let args = {};
      try {
        args = c.json ? JSON.parse(c.json) : {};
      } catch {
        args = { _raw: c.json };
      }
      return { id: c.id || undefined, name: c.name, args };
    });
  const usage =
    inputTokens || outputTokens
      ? { prompt_tokens: inputTokens, completion_tokens: outputTokens }
      : null;
  return { text, calls, usage };
}
