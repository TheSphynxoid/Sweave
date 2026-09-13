# Custom Engine Plan — sweave-native execution layer (best-offer harness)

Status: in progress (2026-09-11; refreshed 2026-09-13 for parallel execution
with the transparency track) — steps 0–4 done 2026-09-13 (step 4: selection
+ fallback, this session); remaining: step 5 (parity gates + docs).
Deepening of the M1.7 side-project note
(`docs/M1_7_PLAN.md` "Side-projects: Custom agent engine" + "Branch notes:
engine driver conversation is side-project-scoped, not R-numbered").
Roadmap slot: extends R3 (multi-harness) — the native engine registers as
a second `Harness` alongside opencode, not as a replacement flag-day.
Companion: `docs/SPECIALIST_VIEW_PLAN.md` (transparency: live pane +
transcript parity on the opencode wire). Parallel-execution discipline:
§7 — read before staffing the second thread.

## 1. Starting point (re-verified 2026-09-11 against code, not older bullets)

- Harness contract is small and ready: `Harness.spawn` / `Harness.attach`
  (+ `get_default_tools` / `health_check`) with `AgentProcess.send` /
  `wait` / `terminate` on the process handle, `Message.model: ModelRef |
  None`, optional `on_chunk` (`sweave/harness/base.py:110-190`).
  `AgentSpec.harness` already selects per task; `harness_registry`
  (`base.py:170-190`) lists offers. `Specialist.harness` already exists
  (default `"opencode"`, `sweave/runtime/specialist_store.py:209`) — step
  4 is selection + fallback semantics only, no schema change (solo
  ruling 2026-09-13: bump iff the field is missing; it is not).
- Opencode is the only spawn-capable harness (DESIGN §5 item 2, §6 R3:
  claude/codex are detect-only). `OpenCodeHarness.spawn` = real subprocess
  + log-file port discovery + v2 HTTP (`sweave/harness/opencode.py:964-1042`).
- "Streaming" today is single-block delivery: `send` concatenates text parts
  and fires `on_chunk` per part (`opencode.py:461-477`); the serve typically
  emits one end-of-turn text object, so `chat.delta` is effectively
  single-shot. The stall watchdog watches message-stream bytes only
  (`specialist_runtime.py:963-964`): 300s body silence, 950s pre-model
  header bound (2026-09-13 — headers vs body are different signals),
  1800s soft outer total, `KILL_ON_SILENCE=False` (2026-09-13 regression:
  byte-silence cannot classify patient-vs-wedged, so the watchdog records
  loudly and lets work continue instead of killing). ACP is strictly worse
  today (no message/thought chunks — DESIGN §R4.2 "ACP verdict").
- Transparency track (2026-09-13, `docs/SPECIALIST_VIEW_PLAN.md`) owns the
  trace vocabulary + detail payload + read-only subchat projection on the
  opencode wire, including the record-side transcript capture (per-turn
  sent-prompt events, `tool.*` parsing in the runtime) and ruling 4B:
  fetch-side engine-truth is deferred to THIS engine, which owns its
  transcript. The engine adopts that vocabulary verbatim (§7) — the
  identical-trace-events invariant now has a named owner.
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
- Working tree is clean except the user's own config hunks (`config.yaml`,
  `models.yaml`, `models.meta.json` — ruling 5 dirt, not ours). Step 0
  landed the new dir `sweave/engine/` (protocol only) + contract tests.
  Shared-doc link edits (M1.7 pointer, DESIGN R3 row, PROJECT_STATE
  entry) ride with the step-5 docs pass.
- Solo-execution re-order (user-locked 2026-09-13 — exclusive event, no
  parallel worker): shared/collision-risk seams first. Order is now
  step 0 (protocol, done) → steps 1 + 3 (free: chat skeleton,
  `build_context`) → view probe step 0 inline (free-tier models only,
  unblocks step 2) → step 2 (tool executor) → step 4
  (selection + fallback, no schema bump) → step 5 (gates + docs).
  Ecosystem baseline first: opencode's own tool/permission/abort/revert
  semantics are adopted verbatim (sources in the Step-0 appendix), never
  re-designed.

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
Control verbs (2026-09-13 — both are load-bearing for the transparency
track, so they are protocol, not later additions): `POST /abort
{session_id}` (consented engine-stop; acknowledged vs UNCONFIRMED outcome,
serves the view track's abort endpoint) and `POST /revert
{session_id, to_message}` (engine rewind for supersede-via-revert §C —
the `revert(to_message)` contract verb; opencode's pointer + shadow-git
semantics are the reference behavior). Session attach/resume is part of
the freeze too (ruling 3: restarts without dropping sessions — the engine
needs a durable session store from day one, which doubles as the
resume-from-partial journal).
Done-gate: protocol doc in this file's appendix + contract tests against
the mock (no engine binary yet); pytest green.
DONE 2026-09-13 (`sweave/engine/protocol.py` + `tests/test_engine_protocol.py`,
27 green; commits `c86e9e7` + `1275ce4`).

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
DONE 2026-09-13 (zero-dep JS sidecar `sweave-engine/src/*.js` +
`sweave/harness/engine.py` adapter + `tests/test_engine_chat.py` 10
green + `scripts/engine_live_gate.py`). Live proof (free-tier, $0):
`liquid/lfm-2.5-2.6b:free` → 24 chunks / 2.6s, output correct,
tokens_used real (in 21 / out 321); `gemma-4-26b-a4b-it:free` →
single-chunk block-mode delivery (forwarded as-is, never faked —
the plan's block-mode clause) then 429 rate-limit, which surfaced
loud as `[chat error: provider_error: …429…]` with no silent loss.
Full suite 871 green (see step-3 note for the 1 deselected
pre-existing UI failure).

Starting shape (recon 2026-09-13, verified against code — next session
starts here, no re-derive): Node v24 + npm 12 present. TS sidecar lives
in `sweave-engine/` (repo-root sibling of the `sweave/engine/`
protocol package — distinct paths, no conflict); Python adapter is
additive (`sweave/harness/engine.py` registering `"sweave-engine"`,
never the default until step 4). Selection sites hardcode
`harness="opencode"` in 4 places today (`chat/loop.py:548`,
`runtime/job_runner.py:702`, `runtime/specialist_runtime.py:739,757`)
— `Specialist.harness` and `config harness.default` exist but no
runtime site reads them; threading them through IS the step-4 work
(no schema change). Step-1 routing stays opt-in per-task override, no
config change. Free-tier live gate: provider `opencode` /
`muse-spark-1.3-contributor-free` (`models.yaml:194-296` block); auth
via the opencode auth-store bootstrap (same store data-dir isolation
already copies).

Auth (user-required 2026-09-13, recorded not rushed — step-1 design
constraint, not a later retrofit): the engine must reach EVERY provider in
the catalog, never a subset. "OpenAI-compatible" is transport convenience,
not a coverage bar — where a catalog provider has no OpenAI-compatible
surface the engine speaks its native protocol (or its gateway); catalog
coverage is the gate. Credential resolution order: explicit config keys →
env → opencode auth-store bootstrap (`auth.json` / `account.json`, already
copied for data-dir isolation — reuse where the provider flow allows);
engine-owned OAuth/device flows long-term. A provider with no usable
credential fails loudly at turn start (named `auth_missing`, never a
mid-turn cryptic error).

### Step 2 — 6-tool executor + permission enforcement (~1.5 sessions)
`read / write+edit / bash(scoped+ask) / glob / grep / todo` in the
delegation worktree cwd. Permission map rendered by the orchestrator
(`agent_permission.py` + `render_external_directory` semantics) enforced
in-engine; `ask` → native `permission.asked` event → existing escalation
flow (answer/skip=deny); no plugin, no hijack endpoint. Identical trace
events (`tool.*`, `step.boundary`, `tokens_used` — vocabulary adopted
verbatim per §7, never invented here) so `sweave log` and
DetailView work unchanged for both engines. Done-gate: scripted tool-turn
fixture (read→edit→bash→grep) green on both harnesses with byte-identical
trace event names; permission ask→allow-once→content and deny→loud-abort
live scenes (mirror of `scripts/m1_12_live_gate.py`).
DONE 2026-09-13 (sidecar `tools.js` + `sweave.js` + `loop.js`, `POST
/api/engine/permission`, adapter delegation_id/role passthrough,
`tests/test_engine_tools.py` 12 + `test_engine_permission_endpoint.py`
17 green; full suite 900 green). Deltas from the plan, all locked by
build evidence: (1) ask needs NO scope re-evaluation — the
orchestrator-rendered map already encodes scope (blind enforcement);
(2) ask_human BLOCKS inside the engine tool call (the engine owns the
ChatLoop's hold-open) and returns the human's answer as the result;
(3) doom-loop degrades to a typed rejection, not a permission ask;
(4) role gating is structural (unoffered tools reject as unknown —
never reach the API). Live proof, free-tier $0
(`scripts/engine_permission_live.py`, ephemeral isolated-home server
so the user's live :8100 was never touched): allow-once → real tool
content; deny → loud failure, no content. Bug found live, fixed +
pinned: loop history slicing dropped the current prompt (provider
400) — the loop now maps the live store every iteration; the hermetic
stub rejects messageless requests like the live provider.

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
like any other section, and the engine receives finished text; corrected
2026-09-13 (user): session-scoped, NOT per-turn — the hook runs pre-turn
but the instruction section is cached per session and re-injected only on
new session, worktree change, file change (mtime/hash-gated), or
post-compaction / post-revert rewind. Unconditional per-turn injection
spams a static file into a session that already remembers it (external
engines carry session memory on top per the M1.7 ruling). The trace
records cached-vs-injected + hash, so staleness is auditable). Skills read here as well
(`skills/{name}/SKILL.md` convention per PLUGGABLES/TRACKING Phase C —
read-not-run v1, native read on this engine, same budgeted traced section
path; the opencode `skill` *tool-execution* parity stays demand-gated,
loading does not). Compaction
rides here too (user-noted 2026-09-11: opencode's hidden compaction agent;
MIT per DESIGN §8 — lift its prompt verbatim with the notice preserved in
THIRD_PARTY_NOTICES, or improve on it — as the engine-side compactor for
within-turn/long-session growth; runtime R6 compaction ownership
unchanged). Done-gate: curated-memory turn shows the audit event;
over-cap turn drops lowest-priority with trace reason; composer tests
extended, engine-agnostic by construction (R4.4 "custom-engine memory API"
note satisfied).
DONE 2026-09-13 (`sweave/chat/context.py`: instruction chain +
session cache + skill index + `ContextBuilder`/`build_context`;
`transcript.py` gains standing sections + `context_audit`;
`loop.py` trace site gains the new keys + `context.built` event;
`tests/test_engine_context.py` 25 green; full suite 861 green.
`context_budget=None` default = no cross-section drops, so current
per-section behavior is byte-identical. Basics standards frozen in
the appendix below; embedder retrieve-then-inject rides R4.4;
compactor implementation rides the engine build.)

### Step 4 — Per-specialist selection + fallback (~0.5 session)
`specialist.harness` field ALREADY EXISTS (default `"opencode"` —
verified 2026-09-13, so no schema bump, no migration: solo ruling,
executor discretion). Step 4 is selection semantics only: default flips
to `sweave-engine` once step 2 lands, per-task override + automatic
fallback to `opencode` on engine failure with `fallback_used` trace
reason. Agents UI badge shows engine per specialist;
no global flag-day. Done-gate: mixed-fleet live scene (native chat +
opencode specialist + native specialist) all `done`; fallback path
covered by killing the engine mid-turn in test.
DONE 2026-09-13 (execution session): `resolve_harness_name()` in
`harness/base.py` (override > mock > specialist > config > opencode,
`harness_selected` trace); `Specialist.harness` + seed YAMLs ×4 +
transients + API/UI create defaults flipped to `sweave-engine`;
`SpecialistRuntime.run()` dispatches (`_run_engine_attempt` vs
`_run_opencode`), per-task override (`POST /api/v2/tasks {harness}`,
400 on unknown, transient side-channel, never persisted);
`POST /api/engine/permission` map rendered per turn
(`render_external_directory` + user roots via ChatLoop/JobRunner);
Agents badge (`HarnessBadge`, success/muted); CLI exports
`SWEAVE_API_URL` so sidecar callbacks hit the right port;
`tests/test_engine_selection.py` 18 green; full suite 918 green
(1 deselected pre-existing UI failure). Amendments (executor,
justified): (1) no stored-record migration — pre-flip `opencode`
values are respected as explicit (a write path always existed via
PUT), only defaults flip; (2) config `harness.default` stays
`opencode` (legacy DelegateTaskTool path frozen; runtime prefers
the record); (3) MCP `defer` takes no harness arg (per-defer
engine choice is scope creep; record selection covers it);
(4) fallback engages ONLY before any work (no tool ran, no text)
— after side effects the error surfaces as
`engine_failed_after_work` (re-running would double-execute);
(5) engine emits no reasoning events (protocol-frozen gap —
thinking blocks stay quiet on native turns, tokens still count);
(6) `Project.default_harness` is write-only display state (nothing
dispatches on it — future tier candidate). Live-scene half of the
done-gate rides step 5 (needs an auth'd server window; hermetic
mixed-fleet + kill-mid-turn pins are green here).

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
- No full opencode tool parity in v1 (`lsp`, `plan`, `webfetch`,
  `websearch`, `patch` stay opencode-only until demand proves otherwise;
  `skill` *tool-execution* likewise — but skill *loading/reads* are step-3
  scope per the paragraph above, not deferred).
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
  class gets its own entry, never a wildcard (M1.12 rule). Hardened by §7:
  the engine's tool executor (step 2) starts only after the transparency
  track's sensor decision lands, so it enforces stable semantics, not
  shifting ones.
- Scope creep on tools — the 6-tool list is the parity bar; everything
  else needs a user ruling with a trace-use audit (count `tool.completed`
  by name from `~/.sweave/traces/*.jsonl` first).
- Test matrix doubling — capped by the identical-trace-events invariant;
  any divergence is a P0 contract bug, not a second suite. Vocabulary
  owner is the transparency track (§7).
- Shared-record coordination — step 4 touches the `Specialist` record
  (the view track's live block reads the same record): additive
  selection semantics only, no schema change (the `harness` field
  predates both tracks). Solo update 2026-09-13: no parallel worker,
  so step 4 lands whenever step 2 does; the §7 gate still binds any
  future second thread.

## 7. Parallel execution with the transparency track (2026-09-13)

Verdict: run both threads side by side — but they share three seams, so
parallelism gets a coupling discipline, not just good intentions.

| Shared seam | Owner | Rule |
|---|---|---|
| Trace event vocabulary (`tool.*`, activity, per-turn prompt, `tokens_used` shapes) | transparency track | engine adopts verbatim; new shapes are proposed to the view plan first, never invented engine-side |
| Detail payload + subchat projection | transparency track | engine turns render through the same projector; engine work that needs a new field extends the payload, never a second surface |
| Permission map + per-tool budget semantics | orchestrator (`agent_permission.py` + view step 1) | engine enforces blindly; step 2 starts after the view sensor decision |

Sequence gates (everything else runs fully parallel):

1. Engine steps 0–1 (protocol + chat skeleton) are free — new dir +
   registry + docs only. May start immediately, alongside any view step.
2. Engine step 2 (tool executor) waits for the view step-1 sensor decision
   (stable tool/activity semantics before building the second enforcer).
3. Engine step 3 (`build_context`) is free (server-side, additive).
4. Engine step 4 (`specialist.harness` field + fallback) coordinates with
   the view live-block work — same record, one migration, ideally one
   session. Neither thread bumps the Delegation/Specialist schema without
   the other reviewing.
5. Opencode contract stays green throughout (ruling 4, 2026-09-11): the
   fallback path runs the full gate every step; a green engine that broke
   opencode is a failed step.

The challenge this answers: building a second tool executor while the
first path's permission model is still being fixed is how two subtly
different sandboxes are born. The gates above make the second executor a
re-implementation of settled semantics, never a parallel invention.

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

## Appendix — opencode capability coverage (2026-09-13)

What "clean/seamless" has to cover, seam by seam — opencode capability on
the left, engine disposition on the right. Anything below the line stays
on opencode (specialists stay there until step-2 parity per ruling 6).

| Opencode surface Sweave depends on | Engine disposition |
|---|---|
| Session create / resume / recreate; per-Session + per-specialist binding | Planned (step 0: session attach/resume in protocol; durable store doubles as partials journal) |
| Per-message model (structured provider/model) + agent pin | Planned (step 1; same provider catalog, model in `POST /run`) |
| Token streaming + reasoning parts (honest granularity) | Planned (step 1 native SSE; block-mode providers stay block-mode, timeout stays honest) |
| 6 tools (read / write+edit / bash / glob / grep / todo) + lifecycle + partial-output capture | Planned (step 2) |
| Permission enforcement (scoped roots, ask → escalation, once/always) | Planned (step 2, orchestrator-rendered map enforced blindly) |
| Sweave tools (defer / list / ask / escalate), identical strings | Planned (steps 1–2, native calls, no MCP hop) |
| Consented abort (acknowledged vs UNCONFIRMED) | Planned (step 0 control verb; serves the view abort endpoint) |
| Revert / rewind (`revert(to_message)` per §C spec) | Planned (step 0 control verb; opencode pointer + shadow-git semantics are the reference) |
| Per-turn `tokens_used` + cost (M1.9 anchor, usage ledger) | Planned (terminal shape identical; per-tool telemetry native) |
| Provider auth for the FULL catalog (no provider left behind) | Partial (step-1 constraint live for openrouter/zai/ollama/gmicloud/nvidia; 2026-09-14 Go slice: `opencode-go` mapped — public `/zen/go/v1` endpoints, pasted-key auth via env/bootstrap, chat/completions live; responses/messages flavors fail loud `bad_request` naming the pending transport; loop-path `resolved.ok` gate closed. Validated-client headers (Go docs "Where can I use it"): `User-Agent: sweave-engine/0.1.0` + stable `x-opencode-session` (durable eng_* id) on every provider call, single-shot and loop iterations, pinned hermetically. Still open: responses transport (unlocks muse-spark-contributor), Zen live proof (chat mapped + hermetic green 2026-09-14; live: Bearer accepted/key valid, but deepseek-free → 400 unavailable and muse-free → 500 — server-side, $0 spent; needs a servable model or one approved paid micro-turn), thinkingmachines/gmi endpoint probes, copilot deferred, engine OAuth long-term). 2026-09-14 credential-ownership (user ruling): `~/.sweave/credentials.json` (0600) is canonical — adopt-once from opencode store (ledgered), drift→pending-import prompts, reverse-sync ours→theirs (backup kept), API-key types only, OAuth detect-only; local/keyless + custom endpoints explicitly out of scope; sidecar reads the Sweave tier (env → sweave → opencode-legacy); `GET /api/providers` = universe × availability; keychain UI rides the next slice |
| AGENTS.md / instruction auto-load (session-scoped, change-gated) | Planned (step 3; automatic with no opt-in, but cached per session — re-inject on new session / worktree / file change / post-compaction only) |
| Skills reads (`skills/{name}/SKILL.md`, read-not-run v1) | Planned (step 3, same budgeted traced path, native read) |
| Compaction + memory `build_context()` | Planned (step 3) |
| Per-specialist selection + opencode fallback | Step 4 done (default flipped, per-task override, fallback-only-before-work). AMENDED 2026-09-14 (user ruling): no automatic cross-harness fallback — fail loud across harnesses, fail over within. Removal queued after the Go slice (gateway models must run natively first, else the flip-period fleet breaks); doctrine: selection sticks, reliability is per-harness (health gate + in-harness provider failover) |
| `lsp` / `plan` / `webfetch` / `websearch` / `patch` tools + `skill` tool-execution | Deferred non-goal (demand-proven only; opencode covers meanwhile — loading/reads are NOT deferred, see above) |
| SubAgentRun ephemeral runs | No engine work (store + endpoints sit above the harness) |
| MCP server | Opencode-adapter only; engine speaks native calls |
| Permission bridge plugin + hijack endpoint | Not transferred (in-engine `permission.asked` replaces the ferry) |

## Appendix — Step-0 protocol freeze (done 2026-09-13)

Frozen in `sweave/engine/protocol.py`, pinned by
`tests/test_engine_protocol.py` (27 green, hermetic — zero I/O).
Baselines adopted verbatim (user ruling: no re-design):

| Decision | Baseline source |
|---|---|
| 6-tool names + `edit`-covers-`write` permission key + `external_directory` + once/always/reject + last-match-wins + per-agent override | opencode tools + permissions docs (fetched 2026-09-13) |
| AbortSignal per tool call; busy-guard 409 mid-turn | opencode `Tool.Context.abort` + `assertNotBusy` (DeepWiki tool-system reference, fetched 2026-09-13) |
| `POST /revert {messageID}` pointer (listing untruncated, next prompt replaces tail) + shadow-git restore + git-only + `unrevert` | `docs/M2_1_FOLLOWUP_PLAN.md` §C live probes on 1.18.30 (`scripts/probe_revert_*.py`, free-tier only) |
| `tool.started/updated/completed/failed {callID, tool, state}` + `step.boundary {reason, cost, tokens{input,output,reasoning,cache{read,write}}}` + terminal `tokens_used {input,output,reasoning,cache_read,cache_write,cost}` | M1.9 audit anchor (`sweave/harness/opencode.py:181-239,508-579`); parity-pinned against the real helper, not a copy |
| `queued:/rejected:/escalated:` contract strings | `sweave/mcp/__init__.py` (defer/ask_human/escalate surfaces) |

Frozen wire (protocol version `"1"`, header
`X-Sweave-Engine-Protocol` on everything; mismatch refuses at
connect via `ProtocolMismatch`, never fails turns cryptically):
`POST /run {session_id, composed_prompt, tools[], permission_map,
model, turn_timeout, cwd}` → SSE `{token, tool.started|updated|
completed|failed, step.boundary, permission.asked, done|error}` +
terminal `tokens_used`; `GET /health` (handshake `{protocol_version,
...}`); `POST /abort {session_id}` → `acknowledged | UNCONFIRMED`
(consented, never gated by `KILL_ON_SILENCE`; 409 when no live turn);
`POST /revert {session_id, to_message}` (whole-message v1, busy-409).
`auth_missing` is a named turn-start failure (full-catalog auth is the
step-1 constraint). `Specialist.harness` predates the freeze — step 4
needs no schema work.

## Appendix — basics standards (frozen 2026-09-13, user-locked: follow,
don't re-design)

The session-stable basics every harness gets identically via
`build_context()` (`sweave/chat/context.py`, step 3). Each row is an
open/ecosystem standard on the left, our adoption delta on the right.
Deltas are naming/roots only — semantics stay verbatim.

| Basic | Standard source (fetched 2026-09-13) | Sweave adoption |
|---|---|---|
| Instruction files | AGENTS.md, Linux-Foundation open standard (60k+ repos, 20+ tools). Plain markdown, no frontmatter. Discovery: global → project root → cwd walk, one file per dir, root-down blank-joined, empty skipped, 32 KiB cap. Nested: nearest wins. | Same semantics. Global root is `~/.sweave/AGENTS.md` (not `~/.codex/`); no `AGENTS.override.md` (promotion discipline covers overrides); `CLAUDE.md` is dir-level fallback with one-level `@`-import resolution. Session-cached (content-gated — same-tick rewrites included; re-inject on new session / worktree change / file change / invalidate). Same 32 KiB cap. |
| Skills | SKILL.md, agentskills.io open spec. `skills/{name}/SKILL.md`, required `name` (1-64, kebab, == dirname) + `description` (1-1024, what+when); optional license/compatibility/metadata/allowed-tools. Progressive disclosure L1 metadata (~100 tok) → L2 body (<5k tok / <500 lines) → L3+ bundled files. | Same validation + disclosure. Roots: `{project}/skills/` → `~/.sweave/skills/` (house project→global order). Read-not-run v1: no execution, zero new MCP tools — L1 index rides the turn, bodies are files the agent reads itself. Matches `docs/PLUGGABLES_PLAN.md` taxonomy (skills are read, never run). |
| Compaction | opencode mechanics (`session/compaction` source + compaction docs, MIT): size-triggered preflight (estimate ≥ limit − max(output, 20k buffer)), `keep.tokens` verbatim tail, anchored summary template (Objective / Important Details / Work State / Next Move / Relevant Files), same-model/no-tools/4k summary cap, prune old completed tool outputs past 40k with `skill` protected, one-shot overflow recovery. Prompts: system `compaction.txt` + "Provide a detailed prompt for continuing…" user text. | Mechanics adopted for the engine-side compactor (engine build scope, not step 3). The verbatim prompts lift with the MIT notice preserved in `THIRD_PARTY_NOTICES` (file created with the compactor — does not exist yet). Step 3 owns only the invalidate hook (`InstructionCache.invalidate`, post-compaction/revert). |
| todo | opencode `todowrite` (`tool/todo.ts` source): full-list write `{content, status, priority}`, statuses pending/in_progress/completed/cancelled, exactly-one-`in_progress` discipline, `todowrite` permission key, disabled for subagents by default. | Engine `todo` tool (step-2 executor scope) mirrors the shape exactly, including the permission key and the subagent default-off. (Our own session `TodoWrite` already follows the same discipline.) |
