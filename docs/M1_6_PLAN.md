# M1.6 — DelegationManager + deferral via MCP tool (execution plan)

Status: in progress. Est. ~2 sessions (MCP integration premium included).
Predecessors: M1.prep → M1.5 all ✅. Rulings locked 2026-08-30:
- **Step 0 done 2026-09-03** (probe passed; full results in `docs/M1_6_STEP0_PROBE.md`):
  per-project `opencode.json` with `mcp.<name> = {type: "local", command: [...],
  environment: {...}, enabled: true, timeout: 30000}` is honored. `opencode
  mcp list` reports "✓ connected" once the opencode process boots in that cwd.
  No global-config injection fallback needed.
- **defer = real MCP tool** (not JSON parsing). Orchestrator calls `defer(target, task)`
  natively; the JSON-convention stays only as an upgrade-path note in this doc.
- **Depth cap 2**: orchestrator → specialist → defer → orchestrator → peer.
- **Chain budget 200K tokens** (config override) — counts **coordination traffic only**:
  orchestrator turns, defer payloads, inter-specialist result summaries. Specialist
  internal work is excluded (principled: cap targets runaway coordination, not work
  quality; practical: opencode's v2 stream exposes no per-turn token counts, so
  specialist-internal usage is opaque).

## Tier framing recap (from M1.2 amendment — binding here)
- Orchestrator: singleton per project; `defer` is its first orchestration tool (M1.7
  wires chat; R6 layers encoder hints).
- Specialist: the work-doer; each has durable session (M1.3).
- Routing: orchestrator LLM decides via `defer`; rule-router = fallback only.

## Architecture

```
User task ──▶ orchestrator delegation (ServeRunner, project cwd)
                │  orchestrator turn (opencode)
                │      └─ calls MCP tool: defer(target="sql-expert", task="…")
                │              │  (stdio JSON-RPC, spawned from opencode config)
                ▼              ▼
          Sweave MCP server (sweave/mcp/server.py, `python -m sweave.mcp`)
                │  localhost HTTP → POST /api/v2/tasks
                │  {parent_task_id, depth+1, budget check, loop check}
                ▼
          DelegationManager.validate ──▶ JobRunner.submit (normal pipeline)
```

The MCP server is a **separate process** (opencode spawns it per config); it talks to
the running Sweave server over localhost REST with a shared token. Delegations created
this way flow through the exact same JobRunner/store/trace machinery as API submissions.

## Steps

### Step 0 — opencode MCP-in-serve probe (~0.25, needs user go)
- Verify: per-project MCP config (`{project}/.opencode/opencode.json` or equivalent)
  makes a stdio MCP server's tools visible inside an `opencode serve` session in that
  cwd; tool call round-trip works.
- Fallback if per-project config unsupported: generate a marked block into the user's
  global `~/.config/opencode/opencode.json` on project activation (idempotent,
  versioned marker; documented in AGENTS if adopted).
- Output: mechanism recorded here; step 3 config plumbing follows the verified path.

### Step 1 — Sweave MCP server (~0.5)
- New package `sweave/mcp/` (`python -m sweave.mcp`, stdio transport, official `mcp`
  SDK — **§8 adoption**: modelcontextprotocol python SDK, MIT, small, no loop
  ownership).
- Tools: `defer(target, task, reason?)` → validates via DelegationManager then
  `POST /api/v2/tasks` (parent_task_id = caller's delegation, explicit agent=target);
  `list_specialists()` → resolved names + one-line description (helps target choice;
  no secrets). Tool results are plain-text success/error — errors are *instructions*
  the orchestrator can act on ("loop detected: sql-expert already in chain").
- Auth: localhost-only + shared token (`~/.sweave/mcp_token`, auto-generated, injected
  into the MCP env via opencode config). Server refuses requests without it.
- Tests: tool handlers against a running test app (httpx ASGI), auth reject, error
  strings.

### Step 2 — DelegationManager + Delegation v3 (~0.5)
- `runtime/delegation_manager.py`: `validate(parent, target, task)` enforcing:
  - **depth**: parent depth + 1 ≤ 2 (orchestrator depth = 0; depth stored on record)
  - **loop**: target not in the active chain set (walk parent_task_id links; cache per
    root in-memory)
  - **budget**: tiktoken estimate of (defer payload + recent chain coordination
    strings) added to the chain accumulator; > 200K (config `routing.chain_budget`)
    → reject. Specialist internal work never enters the accumulator (see ruling).
- Delegation schema v2→v3: add `depth: int = 0`, `chain_root_id: str | None`,
  `coordination_tokens: int = 0`; `from_dict` migration (v2 → defaults). Trace +
  `delegation.status_changed` unchanged.
- Tests: depth reject at 3, loop A→B→A reject, budget accumulation + reject, migration
  v2 records load as v3.

### Step 3 — Orchestrator wiring + parent gating (~0.5)
- `agents/orchestrator/config.yaml` prompt gains the tool contract: "You coordinate.
  Use the defer tool to hand implementation work to a specialist. Never implement
  yourself. After dispatching, end your turn."
- MCP config plumbing per step 0's verified path (orchestrator's serve cwd = project
  root; only the orchestrator gets the MCP server — specialists stay tool-clean).
- **Parent gating**: a delegation with children (any delegation whose
  `parent_task_id`/`chain_root_id` points at it) cannot leave `running` until all
  children reach terminal states; then parent → `review` (human promotes per M1.5
  ruling). Synthesis generation (re-prompting the orchestrator with child results) is
  **M1.7** scope — M1.6 delivers tree lifecycle + gating only.
- Tests: gated parent simulation (mock children), seed prompt contains tool contract.

### Step 4 — Gates + live mini-scene + docs (~0.5)
- pytest (295 + ~20 new), `run.py --check`, `test_full.py`, loader green.
- **Live gate** (gmi, tiny): submit an orchestrator task that requires deferral
  ("Have the backend specialist write a hello.py that prints OK; then tell me it's
  done") → observe: orchestrator turn calls `defer` → child delegation created with
  depth 1 → child reaches review → parent gates → parent review. Trace shows the full
  chain. One loop-attempt probe (negative test) if budget permits.
- Docs: DESIGN §4 rows (DelegationManager ✅, MCP server ✅), R1 M1.6 ✅ + branch
  notes, §8 adoption entry (mcp SDK), PROJECT_STATE progress + rulings, AGENTS
  (MCP token file gotcha if it bites).

## Explicit non-goals
- Synthesis generation / result re-prompting (M1.7).
- Streaming (M1.8). Encoder-assisted target selection (R6).
- Specialist-facing tools via MCP (specialists stay clean; only the orchestrator gets
  defer). Remote/non-localhost MCP transport.

## Risks
- Per-project MCP config support in opencode unverified (step 0 decides; global-config
  injection is the documented fallback, uglier but workable).
- MCP stdio process lifetime vs opencode serve restarts — server must be stateless
  (token + HTTP calls only), so restarts are free.
- Loop/depth checks race under concurrent defers — DelegationManager holds the same
  per-project asyncio.Lock pattern as M1.1 stores.
