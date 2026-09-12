# Custom Engine Plan — sweave-native execution layer (best-offer harness)

Status: planned (2026-09-11). Deepening of the M1.7 side-project note
(`docs/M1_7_PLAN.md` "Side-projects: Custom agent engine" + "Branch notes:
engine driver conversation is side-project-scoped, not R-numbered").
Roadmap slot: extends R3 (multi-harness) — the native engine registers as
a second `Harness` alongside opencode, not as a replacement flag-day.

## 1. Starting point (re-verified 2026-09-11 against code, not older bullets)

- Harness contract is small and ready: `Harness.spawn/send/wait/terminate`,
  `Message.model: ModelRef | None`, optional `on_chunk`
  (`sweave/harness/base.py:110-168`). `AgentSpec.harness` already selects
  per task; `harness_registry` (`base.py:170-186`) lists offers.
- Opencode is the only spawn-capable harness (DESIGN §5 item 2, §6 R3:
  claude/codex are detect-only). `OpenCodeHarness.spawn` = real subprocess
  + log-file port discovery + v2 HTTP (`sweave/harness/opencode.py:964-1042`).
- "Streaming" today is single-block delivery: `send` concatenates text parts
  and fires `on_chunk` per part (`opencode.py:461-477`); the serve typically
  emits one end-of-turn text object, so `chat.delta` is effectively
  single-shot. The 300s stall watchdog (`specialist_runtime.py:80`) and the
  900s turn bound exist because the wire goes silent for minutes then dumps
  a finished block. ACP is strictly worse today (no message/thought chunks —
  DESIGN §R4.2 "ACP verdict").
- Sweave tools (`defer/list_specialists/ask_human/escalate`,
  `sweave/mcp/__init__.py:475-567`) are MCP-stdio only because that is all
  opencode understands. Native opencode tools
  (`bash/read/write/edit/glob/grep/...`, `opencode.py:680-685`) are executed
  by the serve; the M1.12 permission layer (scoped `external_directory`,
  bridge plugin + hijack endpoint, per-agent profiles in
  `runtime/agent_permission.py`) is opencode-wire-specific and does not
  transfer.
- Runtime owns the per-turn composed prompt (`sweave/chat/transcript.py`);
  for external engines the LLM sees runtime prompt + engine session memory
  (M1.7 engine-classes ruling). Delegation store, JobRunner, per-Session
  orchestrator binding, trace JSONL, `TurnDelegations`/`DetailView` all sit
  above the harness and are engine-agnostic — provided both harnesses emit
  identical trace events.
- Working tree is dirty with the parallel session's turn-recovery WIP
  (2026-09-11). This plan creates no conflicts: new dir `sweave-engine/`
  + additive registry entry only. Shared-doc link edits (M1.7 pointer,
  DESIGN R3 row, PROJECT_STATE entry) are deferred to a clean tree.

## 2. Goal state

- `sweave-engine/`: TS sidecar (HTTP, localhost) implementing the `Harness`
  contract against the Python orchestrator. Python keeps ALL knowledge
  (prompt composition, tool schemas + permission roots, model/budget,
  traces); the engine owns execution only (LLM call, token stream, tool
  execution in the worktree cwd).
- Registry offers: `sweave-engine` (best-offer, default for new delegations
  once it reaches step-2 parity) + `opencode` (fallback, per-delegation
  fallback on failure, later opt-in). No flag-day; no Python rewrite.
- True token streaming (liveness = free; watchdog shrinks to a real timeout)
  and a first-class `build_context()` extension point (memory/embedder guide
  with section budget + trace event, not prompt surgery or plugin ferry).
- Sweave tools are native calls on the custom harness (same args +
  `queued:/rejected:/escalated:` strings, no MCP hop, no `sweave_*` prefix
  mangling). The MCP server stays solely as the opencode adapter.

## 3. User-locked rulings (2026-09-11, from planning discussion)

1. Native TS engine sidecar as best-offer; opencode stays as
   baseline/fallback until parity.
2. Orchestrator owns all knowledge; engine owns execution only.
3. Coupled monorepo + versioned localhost protocol; decoupled processes
   (tool execution never shares the API/WS event loop; one cwd per
   specialist; engine restarts without dropping sessions).
4. New tool work lands at the meta layer (contract in `mcp/__init__.py` +
   trace shapes); per-harness adapters only. Opencode quirks frozen (fix
   only if the fallback breaks); opencode contract kept green.
5. Sweave tools native on custom harness, identical contract strings; MCP
   server retained as the opencode adapter.
6. First cut: token stream + 6-tool executor + `build_context()` hook;
   chat/orchestrator path first, specialists stay on opencode until parity.

## 4. Steps

### Step 0 — Protocol freeze (~0.5 session)
Lock the versioned engine↔orchestrator protocol (no implementation):
`POST /run {session_id, composed_prompt, tools[], permission_map, model,
turn_timeout}` → SSE `{token, tool.started|updated|completed|failed,
step.boundary, permission.asked, done|error}` + `tokens_used` terminal
shape identical to the M1.9 audit anchor. `GET /health`, protocol version
header, per-specialist `harness` selection semantics (default + fallback).
Done-gate: protocol doc in this file's appendix + contract tests against
the mock (no engine binary yet); pytest green.

Wire drift (user ruling 2026-09-11: our wire is versioned by us,
theirs drifts under us — asymmetric by construction):
* Native protocol carries an explicit version (header + handshake);
  mismatches refuse loudly at connect, never fail turns cryptically.
* Third-party adapters get the same `Harness` contract PLUS a probe
  gate: version check + capability handshake at runner start
  (session-create → message → terminal shape; reasoning parts?
  permission bus? which terminal signal?) recorded into a
  per-adapter capability map. Behavior branches on the map, never
  on hope. Generalizes the R4.0 `ses_` assertion and the M1.3
  step-0 probes (which were manual) into runtime behavior.
* Trace-rate drift alarms: `sweave doctor` aggregates `incomplete_turn`
  / `stalled` / wire-death rates — a post-upgrade spike IS the drift
  detector, catching the next incident within one turn, not one session.
* Wire knowledge stays quarantined in `harness/<name>.py` (+
  `permission_watch.py` for opencode); drift fixes land there only,
  never in the loop or stores.

### Step 1 — Engine skeleton, chat path, no tools (~1 session)
TS sidecar: `serve --port 0`, direct LLM call (OpenAI-compatible endpoint;
same provider catalog as `models.yaml`), true token SSE → Python `on_chunk`
→ existing `chat.delta` coalescer unchanged. Orchestrator/chat turns only
(Sweave tools native; no worktree tools). Done-gate: live chat turn shows
incremental deltas (multi-`chat.delta` per turn, asserted in test), token
count matches `tokens_used`; opencode fallback untouched; pytest + vitest
green.

### Step 2 — 6-tool executor + permission enforcement (~1.5 sessions)
`read / write+edit / bash(scoped+ask) / glob / grep / todo` in the
delegation worktree cwd. Permission map rendered by the orchestrator
(`agent_permission.py` + `render_external_directory` semantics) enforced
in-engine; `ask` → native `permission.asked` event → existing escalation
flow (answer/skip=deny); no plugin, no hijack endpoint. Identical trace
events (`tool.*`, `step.boundary`, `tokens_used`) so `sweave log` and
DetailView work unchanged for both engines. Done-gate: scripted tool-turn
fixture (read→edit→bash→grep) green on both harnesses with byte-identical
trace event names; permission ask→allow-once→content and deny→loud-abort
live scenes (mirror of `scripts/m1_12_live_gate.py`).

Execution model (2026-09-12): in-process asyncio tasks (structured
concurrency) for orchestration; blocking tool calls offloaded to
worker threads with per-tool timeouts. No fibers — asyncio covers
cooperative scheduling and threads cover true IO parallelism; a
third model adds nothing. Specialists share the parent session's
trust domain (worktree-scoped), so process isolation buys less than
it costs here — but shares fate by construction: a wedged native
call must trip its tool timeout, never the loop (a stuck loop wedges
EVERY session, the failure mode subprocesses never had). Untrusted
code still wants a boundary — sandbox stays deferred per plan, and
opencode specialists stay subprocess-isolated regardless.

Timeout mitigation requirements (2026-09-12 — every 300s-class
incident to date was transport-phase, not work-phase: headers never
arrived, zero bytes, root cause always opencode/model-side and
unobservable from Sweave). In-process eliminates the whole class
(no HTTP, no headers, no httpx races), and the engine must additionally
guarantee no silent death ever again:

1. No transport phases exist: a stuck tool is directly observable
   (thread state + timing), never inferred from byte silence.
2. Per-tool timeouts with partial-output capture (a timed-out tool
   keeps what it produced; the turn degrades, it doesn't vanish).
3. Progress heartbeats from the executor (tool started / first
   output): silence is measured against expected progress, and the
   watchdog trips on stalled progress, not on a flat clock.
4. Graceful abort: stop generation, keep partials + trace — never
   kill-and-lose-everything (the 300s transport loss pattern).
5. Resume-from-partial: tool results checkpoint incrementally (the
   event-sourced trace already supports this), so a retried turn
   continues past completed tools instead of redoing them.
6. Provider timeout + fallback (multi-provider registry already
   exists): a hung provider fails over instead of hanging the turn.

What in-process does NOT fix (still needed): zero-byte model hangs
(loop detector, planned), genuinely slow work (budgets + estimation,
M2.0 landed), admission control. Name: `sweave-engine` (matches
`sweave-orchestrator` / `sweave-specialist`; Step 4 already uses it
as the `specialist.harness` default).

Tool-context budget (standing, from the 2026-09-12 audit: MCP surface
2,880 chars, defer alone 1,152 — descriptions are the fat). Native
advantages the MCP wire cannot match: (1) short schemas by default —
the orchestrator prompt already teaches the contract, so tool text
stays reference-tight; (2) dynamic pruning per turn state (hide
`defer` at depth cap instead of describing the rejection; hide
orchestration tools from specialists structurally, not via
deny-lists); (3) endgame is a native `delegate` op in the turn
grammar, not a tool call at all — zero schema overhead, typed
errors, server-side chain enforcement before the model spends a
token; (4) the context audit becomes native telemetry (per-tool,
per-turn in/out tokens on the trace). Budgets per surface pinned in
tests so overhead can't silently regrow.

### Step 3 — `build_context()` extension point (~0.5 session)
Pre-turn hook owned by the orchestrator: memory/embedder retrieval runs
server-side with section budget + `context.built {sections, tokens,
dropped}` trace event; the engine receives finished context, never builds
it. The embedder guide lands here (retrieve-then-inject with scores), not
as prompt surgery. Instruction files land here too (user-noted 2026-09-11:
opencode auto-loads `AGENTS.md`; the ecosystem convention varies —
`CLAUDE.md`/`AGENTS.md` per harness — so the orchestrator loads
`{project}/AGENTS.md` + `{worktree}/AGENTS.md` itself, budgeted and traced
like any other section, and the engine receives finished text). Compaction
rides here too (user-noted 2026-09-11: opencode's hidden compaction agent;
MIT per DESIGN §8 — lift its prompt verbatim with the notice preserved in
THIRD_PARTY_NOTICES, or improve on it — as the engine-side compactor for
within-turn/long-session growth; runtime R6 compaction ownership
unchanged). Done-gate: curated-memory turn shows the audit event;
over-cap turn drops lowest-priority with trace reason; composer tests
extended, engine-agnostic by construction (R4.4 "custom-engine memory API"
note satisfied).

### Step 4 — Per-specialist selection + fallback (~0.5 session)
`specialist.harness` field (default `sweave-engine` once step 2 lands,
per-task override + automatic fallback to `opencode` on engine failure with
`fallback_used` trace reason). Agents UI badge shows engine per specialist;
no global flag-day. Done-gate: mixed-fleet live scene (native chat +
opencode specialist + native specialist) all `done`; fallback path
covered by killing the engine mid-turn in test.

### Step 5 — Parity gates + docs (~0.5 session)
Full pytest + vitest + `run.py --check` + `npm run build` 2× green; live
gates (chat streaming, tool turn, permission ask/deny, fallback) recorded.
Docs: DESIGN R3 row (native engine ✅, opencode fallback), §4 component
rows, PROJECT_STATE entry, GOTCHAS (engine wire + permission event
shapes). Opencode drops to opt-in only after this step — not before.
Explicitly deferred: `lsp/skill/plan/web*` parity, ACP surface, Python
rewrite, high-level framework adoption (§8: engine is tool executor, not
orchestrator — policy holds).

## 5. Explicit non-goals

- No Python backend rewrite; no merging the engine into the API process.
- No full opencode tool parity in v1 (`lsp`, `skill`, `plan`, `webfetch`,
  `websearch`, `patch` stay opencode-only until demand proves otherwise).
- No fork of opencode (MIT reference clone stays read-only inspiration).
- No LangGraph/CrewAI/AutoGen/Agents-SDK adoption (§8: they own the loop).
- No ACP harness (verdict stands until opencode streams session updates).
- No changes to promotion/merge policy (human promotes, human merges).
- No OS-level filesystem sandbox in v1 (user ruling 2026-09-11:
  AppContainer/integrity-levels = non-goal roadmap, nice-to-have, not
  urgent). Sandbox phasing: software sandbox now (per-call roots +
  permission maps, zero native code) → Windows Job Objects helper next
  (kill-on-close + resource limits, DESIGN §8 native candidate) →
  AppContainer/integrity later, if ever. Linux note (2026-09-11):
  `bubblewrap`/Landlock hardening is R5 backlog (opt-in, alongside
  cross-platform verification) — easier than Windows, but the uniform
  software sandbox stays the cross-platform guarantee so "sandboxed"
  never means different things per OS.

## 6. Risks

- Provider streaming granularity varies (cf. the `inkling:free` single-SSE
  lesson, DESIGN R4.2) — engine must handle block-mode providers without
  reintroducing fake streaming; liveness timeout stays honest.
- Permission parity drift between engines — mitigated by the orchestrator
  rendering one map and both engines enforcing it blindly; any new `ask`
  class gets its own entry, never a wildcard (M1.12 rule).
- Scope creep on tools — the 6-tool list is the parity bar; everything
  else needs a user ruling with a trace-use audit (count `tool.completed`
  by name from `~/.sweave/traces/*.jsonl` first).
- Test matrix doubling — capped by the identical-trace-events invariant;
  any divergence is a P0 contract bug, not a second suite.
- Parallel-session conflicts — this plan touches only new paths until
  step 4's `specialist.harness` field (schema bump + migration per gotcha
  #12 rule); coordinate that step with the turn-recovery WIP owner.

## Appendix — transport map (contract identical, transport differs)

| Concern | Opencode harness (fallback) | Native engine (best-offer) |
|---|---|---|
| Prompt in | composed prompt as user message on serve session | composed prompt in `POST /run` body |
| Tokens out | chunked-JSON parts, glued | SSE tokens, native |
| Native tools | serve executes, parts-traced | engine executes, same event names |
| Sweave tools | MCP stdio (`sweave_*`, token header) | native calls, same strings |
| Permission ask | bridge plugin → hijack endpoint | `permission.asked` event → escalation store |
| Session | `ses_*` id + 404-recreate | engine session id, orchestrator still binds per Session/Specialist |
| Trace | harness reader projects events | engine emits, runtime projects identically |
