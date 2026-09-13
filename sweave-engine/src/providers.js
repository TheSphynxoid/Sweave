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
  // engine speaks chat/completions only — models on the sibling
  // flavors fail loud in resolveProvider (named, at turn start),
  // never as a mid-turn provider 400. Flavor source: the Go docs
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
  // Catalog providers WITHOUT an OpenAI-compatible surface (honest
  // auth_missing, never attempted): github-copilot (SDK device flow,
  // deferred by user ruling), cloudflare-workers-ai (workers binding,
  // no HTTP key surface), opencode (Zen pay-as-you-go — same key
  // shape as Go, own flavor map, own slice), thinkingmachines / gmi
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
 * { ok, baseURL, key, via } or { ok: false, code, reason }.
 *
 * `modelId` selects the wire flavor on multi-flavor gateways
 * (opencode-go): chat/completions is attempted, responses/messages
 * models fail loud here with code "bad_request" naming the pending
 * transport — a vocabulary-frozen code, never a new event shape.
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
  // Wire-flavor gate FIRST (before credentials): a model on a flavor
  // the engine doesn't speak must fail here even when a key exists —
  // otherwise it dies mid-turn as a provider 400.
  if (spec.flavors && modelId) {
    for (const [flavor, ids] of Object.entries(spec.flavors)) {
      if (ids.has(modelId)) {
        return {
          ok: false,
          code: "bad_request",
          reason:
            `model ${JSON.stringify(modelId)} on ${JSON.stringify(provider)} ` +
            `needs the ${flavor} transport (pending; engine speaks ` +
            `chat/completions only)`,
        };
      }
    }
  }
  const baseURL = baseOverride || spec.baseURL;
  if (spec.key === false) {
    return { ok: true, baseURL, key: null, via: "no-key (local serve)" };
  }
  const explicit = process.env[`SWEAVE_ENGINE_KEY_${provider.toUpperCase().replace(/[^A-Z0-9]/gi, "_")}`];
  if (explicit) return { ok: true, baseURL, key: explicit, via: "env:SWEAVE_ENGINE_KEY_*" };
  for (const k of spec.envKeys) {
    if (process.env[k]) return { ok: true, baseURL, key: process.env[k], via: `env:${k}` };
  }
  const own = sweaveStoreKey(provider);
  if (own) return { ok: true, baseURL, key: own.key, via: own.via };
  const boot = bootstrapKey(provider);
  if (boot) return { ok: true, baseURL, key: boot.key, via: boot.via };
  return {
    ok: false,
    code: "auth_missing",
    reason:
      `no credential for provider ${JSON.stringify(provider)} ` +
      `(checked SWEAVE_ENGINE_KEY_*, ${spec.envKeys.join("/")} , sweave store, opencode auth-store)`,
  };
}

export const TOOL_BASELINE = ["read", "edit", "write", "bash", "glob", "grep", "todo"];
export const SWEAVE_NATIVE_TOOLS = ["defer", "list_specialists", "ask_human", "escalate"];
export const KNOWN_TOOLS = new Set([...TOOL_BASELINE, ...SWEAVE_NATIVE_TOOLS]);
