# defer → delegate full rename — plan of record (2026-09-15)

Status: ruled, NOT started (ordered: begin only after the running
sweave session completes — the rename touches the tool the model
calls mid-turn, so no live turn may exist when it lands; server
restart at the end).

## Ruling (user-locked 2026-09-15)
Full rename, no alias period: the tool `defer` becomes `delegate`
everywhere at once. Rationale: "defer" reads as postpone (and since
chat-defers join by default, the wrong reading is behaviorally
half-true); the codebase's own noun is already delegation
(`DelegationManager`, delegation records, `caller_delegation_id`,
legacy `DelegateTaskTool`) — `defer` is the odd one out.

## Scope (sized 2026-09-15: 244 `\bdefer\b` hits, most prose)
Load-bearing (must change together, one commit):
- `sweave/mcp/__init__.py` — MCP tool name + spec text.
- `sweave-engine/src/sweave.js` + tool defs (`EXEC_TOOL_DEFS`/
  sweave-tool tables, whichever names `defer`) + `SWEAVE_TOOL_NAMES`
  in `loop.js`.
- `sweave/runtime/agent_permission.py` — `sweave_defer` key
  (orchestrator allow / specialist deny) + profiles.
- Orchestrator seed prompt (`sweave/agents/orchestrator/config.yaml`)
  defer spec → delegate spec (args identical, only the name +
  one line killing the postpone reading for good).
- `POST /api/engine/permission` + bridge/escalation question text
  that names the tool; `sweave log` labels.
- Tests calling the tool by name (engine-tools ask scenes,
  permission endpoint tests, MCP server tests) + docs
  (`docs/*_PLAN.md` prose, `DESIGN.md` tool table, `AGENTS.md`
  key files if they name it).
- Managed islands regenerate (`ensure_mcp_config` pump + restart);
  stale `opencode.json` islands must not resurrect `defer`.

Deliberately NOT changed:
- Historical traces keep `defer` tool names (immutable audit trail);
  read-side code matching tool names must tolerate both during the
  transition window (grep before assuming).
- `caller_delegation_id`, `DelegationManager`, record schema: already
  delegate-vocabulary, untouched.
- UI hits are prose/comments only (`Thread.tsx`, `CommandPalette`,
  `EditSpecialistDialog` placeholder) — reword, no logic.

## Steps
1. `git status` clean of other threads; no live turn
   (`running` delegations + `/turn` snapshots empty).
2. Mechanical rename across the load-bearing list (no behavior
   change; args/contract strings identical).
3. Prose sweep (docs, prompts, UI strings, comments).
4. Gates: touched suites + full pytest + `run.py --check` + UI
   build + a live ask-scene proving the renamed tool end to end.
5. Restart server (resident MCP config + sidecar), re-verify.

## Non-goals
- No alias/shim period (ruled out: one vocabulary, not two).
- No arg or semantics change in the same commit.
