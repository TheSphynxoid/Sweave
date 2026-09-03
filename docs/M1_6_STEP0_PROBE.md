# M1.6 step 0 — opencode MCP-in-serve probe results

Date: 2026-09-03. opencode 1.18.27. Per-project MCP config: **works**.

## TL;DR
- Per-project `opencode.json` with `mcp.<name> = {type: "local", command: [...], environment: {...}, enabled: true, timeout: 30000}` is honored.
- `opencode mcp list` reports the per-project server as **✓ connected** once the opencode process boots in that cwd.
- Tool listing + tool calls round-trip through the MCP protocol (initialize → tools/list → tools/call).
- Sweave's MCP server (M1.6 step 1) is the only consumer of this seam; specialists stay tool-clean, the orchestrator's serve cwd carries the MCP config.

## Empirical run

Setup:
- Created a tmp probe project: `C:\Users\user\AppData\Local\Temp\opencode\probe_project\opencode.json`
  with `mcp.sweave-probe` declared as a local stdio MCP server (a one-file Python probe that
  registers a `probe_echo` tool via the `mcp` 2.1 SDK).
- Ran `opencode mcp list` from the probe project dir.
- Result: the per-project config was loaded; the server was discovered and reached "connected" status.

```
┌  MCP Servers
│
●  ○ web-search-prime  disabled
│      https://api.z.ai/api/mcp/web_search_prime/mcp
│
●  ○ zread  disabled
│      https://api.z.ai/api/mcp/zread/mcp
│
●  ✓ sweave-probe  connected
│      python C:\Users\user\AppData\Local\Temp\opencode\probe_mcp.py
│
└  3 server(s)
```

`opencode debug config` from the same cwd shows the merged config including the per-project
entry, confirming the precedence model from the docs (project overrides global):

```json
"mcp": {
  ...
  "sweave-probe": {
    "type": "local",
    "command": ["python", "C:\\Users\\user\\AppData\\Local\\Temp\\opencode\\probe_mcp.py"],
    "enabled": true,
    "timeout": 30000
  }
}
```

## Lessons burned (avoid the opencode MCP foot-guns)

1. **`type` must be `"local"` (not `"stdio"`)**. The generic MCP docs use `"stdio"`; opencode rejects it.
2. **`command` is an array, not a string with separate `args`**. Both shapes are documented elsewhere — opencode only takes the array.
3. **Env vars go under `environment`, not `env`**. Common readme shape uses `env`; opencode silently drops it.
4. **`timeout: 30000` is needed for first-fetch** (default 5000ms is too low for a slow `python.exe` startup, especially on Windows).
5. **`opencode mcp add` CLI does NOT support local stdio** — it only adds remote (`--url`) servers. Local stdio must be declared in the config file directly.
6. **The user's global `~/.config/opencode/opencode.json` currently has only remote MCP entries** (both disabled). Adding a local entry at the project level is a clean additive change; the global config is untouched.

## Decision

Plan §Step 0's "verify the mechanism" gate is **passed**. We use the per-project
`opencode.json` path (no global-config injection fallback needed). The
plumbing is:
- M1.6 step 1 builds `sweave/mcp/` (the stdio MCP server).
- M1.6 step 3's MCP config plumbing writes the per-project `opencode.json` from
  the orchestrator's project root, with the sweave MCP entry injected
  (idempotently — versioned marker so re-project-activation is a no-op).

## Open follow-up (not blockers)

- The probe SDK pattern (`add_request_handler(method, params_type, handler)`)
  is the v2.1 API. Older decorator-style examples in some MCP docs are stale.
  We pin the SDK version in `pyproject.toml` so this doesn't drift.
- A future step could expose a tiny `opencode mcp tools <name>` CLI for listing
  per-server tools without booting a session; not needed for M1.6.
