// Provider table + credential resolution for sweave-engine.
//
// Credential ownership (user ruling 2026-09-14): Sweave owns keys in
// ~/.sweave/credentials.json. Resolution order: explicit per-provider
// env (SWEAVE_ENGINE_KEY_<NAME>) -> conventional env (<NAME>_API_KEY,
// see ENV_KEYS) -> Sweave store -> opencode auth-store bootstrap
// (legacy import source; converges via server-side adopt prompts).
// Base-URL override per provider via SWEAVE_ENGINE_BASE_<NAME>
// (tests point this at a localhost stub).

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

// Validated-client contract (Go docs "Where can I use it"): identify
// with our own user agent (never a generic SDK/HTTP-library name)
// and send a stable x-opencode-session per conversation (routing +
// prompt caching). Our durable eng_* session ids are exactly that.
export const ENGINE_USER_AGENT = "sweave-engine/0.1.0";
export const SESSION_HEADER = "x-opencode-session";

// Catalog providers with a KNOWN OpenAI-compatible chat-completions
// surface. `key: false` = no credential needed (local serve).
const TABLE = {
  openrouter: { baseURL: "https://openrouter.ai/api/v1", envKeys: ["OPENROUTER_API_KEY"] },
  zai: { baseURL: "https://api.z.ai/api/paas/v4", envKeys: ["ZAI_API_KEY", "Z_AI_API_KEY"] },
  ollama: { baseURL: "http://localhost:11434/v1", key: false, envKeys: [] },
  gmicloud: { baseURL: "https://api.gmi-serving.com/v1", envKeys: ["GMI_API_KEY"] },
  nvidia: { baseURL: "https://integrate.api.nvidia.com/v1", envKeys: ["NVIDIA_API_KEY"] },
  // OpenCode Go ($10/mo subscription, API key from the Zen console):
  // public endpoints under /zen/go/v1 (opencode.ai/docs/go). The
  // engine speaks chat/completions, the Responses API, and the
  // Anthropic Messages API — models on the remaining flavors fail
  // loud in resolveProvider (named, at turn start), never as a
  // mid-turn provider 400. Flavor source: the Go docs
  // endpoint table; unknown future ids are ATTEMPTED on
  // chat/completions (a Go 4xx then surfaces loudly anyway).
  "opencode-go": {
    baseURL: "https://opencode.ai/zen/go/v1",
    envKeys: ["OPENCODE_GO_API_KEY"],
    flavors: {
      responses: new Set([
        "grok-4.6",
        "gpt-5.6-luna",
        "muse-spark-1.3-contributor",
        "muse-spark-1.2-contributor",
      ]),
      messages: new Set([
        "minimax-m3",
        "minimax-m2.7",
        "minimax-m2.5",
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen3.6-plus",
      ]),
    },
  },
  // OpenCode Zen (pay-as-you-go; same gateway family as Go, key
  // from the Zen console): public endpoints under /zen/v1. Flavor
  // source: the Zen gateway table (gpt → responses, claude →
  // messages, gemini → google, compatible → chat/completions).
  // Unknown ids are ATTEMPTED on chat/completions (loud on mismatch).
  // LIVE 2026-09-14: Bearer accepted, key valid; deepseek-v4-flash-free
  // → 400 "Model is unavailable", muse-spark-1.3-contributor-free →
  // 500 (both $0, pre-generation) — server-side availability, not a
  // client bug. Full native turn still unproven live; needs a
  // servable model (TUI /models is ground truth, or one paid
  // micro-turn on glm-5.3-flash with approval).
  "opencode": {
    baseURL: "https://opencode.ai/zen/v1",
    envKeys: ["OPENCODE_API_KEY"],
    flavors: {
      responses: new Set([
        "gpt-5", "gpt-5-codex", "gpt-5-nano", "gpt-5.1",
        "gpt-5.1-codex", "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
        "gpt-5.2", "gpt-5.2-codex", "gpt-5.3-codex",
        "gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.4-mini",
        "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.5", "gpt-5.5-pro",
        "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra",
      ]),
      messages: new Set([
        "claude-3-5-haiku", "claude-fable-5", "claude-fable-5-1",
        "claude-haiku-4-5", "claude-opus-4-1", "claude-opus-4-5",
        "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
        "claude-opus-5", "claude-sonnet-4", "claude-sonnet-4-5",
        "claude-sonnet-4-6", "claude-sonnet-5",
      ]),
      google: new Set([
        "gemini-3-flash", "gemini-3-pro", "gemini-3.1-pro",
        "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash",
        "gemini-3.7-flash", "gemini-3.8-flash",
      ]),
    },
  },
  // Catalog providers WITHOUT an OpenAI-compatible surface (honest
  // auth_missing, never attempted): github-copilot (SDK device flow,
  // deferred by user ruling), cloudflare-workers-ai (workers binding,
  // no HTTP key surface), thinkingmachines / gmi
  // (endpoint unknown until proven).
};

function authStorePaths() {
  const home = homedir();
  const xdg = process.env.XDG_DATA_HOME || join(home, ".local", "share");
  return [
    join(home, ".sweave", "opencode-data", "opencode", "auth.json"),
    join(xdg, "opencode", "auth.json"),
  ];
}

function bootstrapKey(provider) {
  for (const p of authStorePaths()) {
    try {
      if (!existsSync(p)) continue;
      const store = JSON.parse(readFileSync(p, "utf8"));
      const entry = store[provider];
      if (entry && typeof entry.key === "string" && entry.key.length > 0) {
        return { key: entry.key, via: `auth-store:${p}` };
      }
    } catch {
      // A corrupt store is not our secret to keep — fall through to
      // auth_missing with the path recorded, never crash the turn.
    }
  }
  return null;
}

function sweaveStoreKey(provider) {
  try {
    const p = join(homedir(), ".sweave", "credentials.json");
    if (!existsSync(p)) return null;
    const store = JSON.parse(readFileSync(p, "utf8"));
    const entry = store && store.providers && store.providers[provider];
    if (entry && entry.type === "api_key" && typeof entry.key === "string" && entry.key.length > 0) {
      return { key: entry.key, via: "sweave-store" };
    }
  } catch {
    // Unreadable own-store: fall through to the legacy bootstrap
    // (which may still hold an adopted copy), never crash the turn.
  }
  return null;
}

/**
 * Resolve how to call `provider` for `modelId`. Returns
 * { ok, baseURL, key, via, flavor } or { ok: false, code, reason }.
 *
 * `modelId` selects the wire flavor on multi-flavor gateways: "chat"
 * (chat/completions) or "responses" (Responses API). Flavors the
 * engine doesn't speak (messages, google) fail loud here with code
 * "bad_request" naming the pending transport — a vocabulary-frozen
 * code, never a new event shape. The gate runs before credentials so
 * a keyed-but-unspeakable model still fails at turn start.
 */
export function resolveProvider(provider, modelId) {
  const spec = TABLE[provider];
  const baseOverride = process.env[`SWEAVE_ENGINE_BASE_${provider.toUpperCase().replace(/[^A-Z0-9]/gi, "_")}`];
  if (!spec) {
    return {
      ok: false,
      code: "auth_missing",
      reason: `provider ${JSON.stringify(provider)} has no OpenAI-compatible surface mapped (native protocol pending)`,
    };
  }
  let flavor = "chat";
  if (spec.flavors && modelId) {
    let known = false;
    for (const [name, ids] of Object.entries(spec.flavors)) {
      if (!ids.has(modelId)) continue;
      known = true;
      // The engine speaks chat/completions, the Responses API, and
      // the Anthropic Messages API. Remaining flavors (google)
      // fail loud here with code "bad_request" naming the pending
      // transport — a vocabulary-frozen code, never a new event
      // shape. The gate runs before credentials so a keyed-but-
      // unspeakable model still fails at turn start.
      if (name === "responses" || name === "messages") {
        flavor = name;
        break;
      }
      return {
        ok: false,
        code: "bad_request",
        reason:
          `model ${JSON.stringify(modelId)} on ${JSON.stringify(provider)} ` +
          `needs the ${name} transport (pending; engine speaks ` +
          `chat/completions + responses + messages)`,
      };
    }
    // Drift telemetry (hygiene 2026-09-20): the flavor tables are
    // hardcoded and the provider catalog drifts — an unknown id is
    // still ATTEMPTED on chat/completions (a Go 4xx surfaces loudly
    // anyway), but the attempt is named on stderr so a wrong-flavor
    // guess is diagnosable instead of mysterious.
    if (!known) {
      try {
        process.stderr.write(
          `sweave-engine: model ${JSON.stringify(modelId)} not in the ${provider} flavor table; attempting chat/completions\n`
        );
      } catch {}
    }
  }
  const baseURL = baseOverride || spec.baseURL;
  if (spec.key === false) {
    return { ok: true, baseURL, key: null, via: "no-key (local serve)", flavor };
  }
  const explicit = process.env[`SWEAVE_ENGINE_KEY_${provider.toUpperCase().replace(/[^A-Z0-9]/gi, "_")}`];
  if (explicit) return { ok: true, baseURL, key: explicit, via: "env:SWEAVE_ENGINE_KEY_*", flavor };
  for (const k of spec.envKeys) {
    if (process.env[k]) return { ok: true, baseURL, key: process.env[k], via: `env:${k}`, flavor };
  }
  const own = sweaveStoreKey(provider);
  if (own) return { ok: true, baseURL, key: own.key, via: own.via, flavor };
  const boot = bootstrapKey(provider);
  if (boot) return { ok: true, baseURL, key: boot.key, via: boot.via, flavor };
  return {
    ok: false,
    code: "auth_missing",
    reason:
      `no credential for provider ${JSON.stringify(provider)} ` +
      `(checked SWEAVE_ENGINE_KEY_*, ${spec.envKeys.join("/")} , sweave store, opencode auth-store)`,
  };
}

export const TOOL_BASELINE = ["read", "edit", "write", "bash", "glob", "grep", "todo", "git"];
export const SWEAVE_NATIVE_TOOLS = ["defer", "list_specialists", "ask_human", "escalate"];
export const KNOWN_TOOLS = new Set([...TOOL_BASELINE, ...SWEAVE_NATIVE_TOOLS]);

/**
 * Map a model `+variant` suffix to a reasoning-effort request.
 *
 * Returns `{ effort }` (verbatim value: low/medium/high/minimal/max/
 * xhigh and future provider-specific values ride as-is — the request
 * either applies or fails loud with a compat fallback, never silent),
 * `{ none: true }` for thinking-off, or null when absent/empty.
 * Verbatim passthrough is deliberate: the variant registry is
 * provider-owned and grows without us (yesterday's unknown value is
 * tomorrow's effort level); the strip-and-retry fallback at each
 * transport keeps a wrong guess to one wasted call, never a bricked
 * turn.
 */
export function reasoningEffortFor(variant) {
  if (variant === undefined || variant === null) return null;
  const v = String(variant).trim();
  if (!v) return null;
  if (v.toLowerCase() === "none") return { none: true };
  return { effort: v };
}

/**
 * True when a provider 400 names reasoning/effort AND the attempt
 * actually sent reasoning features (replayed thinking or an effort
 * request). Guards the strip-and-retry fallback: without the sent
 * flag, an unrelated 400 mentioning "effort" would burn a pointless
 * second attempt.
 */
export function shouldStripReasoningFeatures(err, sent) {
  if (!err || err.status !== 400 || !sent) return false;
  const text = `${err.message || ""}\n${err.body || ""}`;
  return /reasoning|effort/i.test(text);
}
