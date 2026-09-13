// Provider table + credential resolution for sweave-engine.
//
// Step-1 design constraint (user-locked): the engine must reach EVERY
// provider in the catalog, never a subset. "OpenAI-compatible" is
// transport convenience, not a coverage bar. Every catalog provider
// resolves to either a working OpenAI-compatible endpoint or a NAMED
// auth_missing failure at turn start — never a mid-turn cryptic error.
//
// Credential order: explicit per-provider env (SWEAVE_ENGINE_KEY_<NAME>)
// -> conventional env (<NAME>_API_KEY, see ENV_KEYS) -> opencode
// auth-store bootstrap (auth.json: the isolated data-dir copy first,
// then the user's real store). Base-URL override per provider via
// SWEAVE_ENGINE_BASE_<NAME> (tests point this at a localhost stub).

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
  // Catalog providers WITHOUT an OpenAI-compatible surface (honest
  // auth_missing, never attempted): github-copilot (SDK device flow),
  // cloudflare-workers-ai (workers binding, no HTTP key surface),
  // opencode / opencode-go (builtin gateway endpoints live inside the
  // opencode binary — unresolved from out here), thinkingmachines /
  // gmi / others (endpoint unknown until proven).
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

/**
 * Resolve how to call `provider`. Returns { ok, baseURL, key, via } or
 * { ok: false, code: "auth_missing", reason }.
 */
export function resolveProvider(provider) {
  const spec = TABLE[provider];
  const baseOverride = process.env[`SWEAVE_ENGINE_BASE_${provider.toUpperCase().replace(/[^A-Z0-9]/gi, "_")}`];
  if (!spec) {
    return {
      ok: false,
      code: "auth_missing",
      reason: `provider ${JSON.stringify(provider)} has no OpenAI-compatible surface mapped (native protocol pending)`,
    };
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
  const boot = bootstrapKey(provider);
  if (boot) return { ok: true, baseURL, key: boot.key, via: boot.via };
  return {
    ok: false,
    code: "auth_missing",
    reason:
      `no credential for provider ${JSON.stringify(provider)} ` +
      `(checked SWEAVE_ENGINE_KEY_*, ${spec.envKeys.join("/")} , opencode auth-store)`,
  };
}

export const TOOL_BASELINE = ["read", "edit", "write", "bash", "glob", "grep", "todo"];
export const SWEAVE_NATIVE_TOOLS = ["defer", "list_specialists", "ask_human", "escalate"];
export const KNOWN_TOOLS = new Set([...TOOL_BASELINE, ...SWEAVE_NATIVE_TOOLS]);
