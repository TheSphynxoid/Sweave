# Sweave — System Design

> Rebuilt 2026-08-29 after the original design was lost. Lineage: Sweave began as a
> **Polly fork** (Polly = Omnigent's multi-agent coding orchestrator). Decision: Sweave is
> now a **standalone, Polly-style orchestrator** — Omnigent's agent YAML spec is a
> compatibility target, not a dependency. Status markers: ✅ wired · ⚠️ stub · ❌ dead ·
> 📐 planned. Source of truth for "what runs today": PROJECT_STATE.md + the table in §4.

## 1. Vision

Sweave orchestrates a supervisor conversation (the **Session**) that delegates work to
persistent **specialist agents** (backend / frontend / reviewer / custom), each running in
an isolated **git worktree**, opening a **PR as its deliverable**. A human always merges.
Long-term memory is hierarchical: global → project → session.

Non-negotiable principles (inherited from Polly):
1. **The orchestrator never writes code.** It decomposes, delegates, synthesizes.
2. **Worktree isolation.** Every delegated task runs in `.worktrees/{task_id}/{agent}`.
3. **The PR is the deliverable.** Agents never merge; cross-review before "done".
4. **Human merges to main.** `git merge` / `gh pr merge` into the base branch are
   human-only actions. Exception (user-locked 2026-08-29): specialists may auto-merge
   clean work into per-task **integration branches** after the Stage-0 overlap check and
   cross-review pass — the base branch itself is never auto-merged.
5. **Harness-agnostic specs.** Agents are declared in YAML; the executor is swappable
   (OpenCode today; Claude Code / Codex on the roadmap).

Distinct from Polly (our additions): project-scoped everything (agents, memory, sessions
per folder on disk), hierarchical memory banks, model routing by role + rules, Windows-first.

## 2. Core concepts

```
Project   a folder on disk, opened via backend file browser; owns agents, sessions, memory
Session   an orchestrated conversation inside a project (parent = supervisor)
Specialist  a PERSISTENT, DYNAMICALLY CREATED worker (per project or globally): identity +
            durable context + current_model; idle between tasks; conversation resumes.
            NOT a fixed role taxonomy — users create their own. Sole exception: the
            orchestrator, a singleton (one supervisor per project).
Delegation  PERSISTENT record of implementation work delegated to a specialist:
            worktree, branch, PR URL, status (queued→running→review→done/failed), cost.
            The deferral tree and /fanout nodes are Delegations. Persisted per project
            since M1.1 (schema v2); UI v1 compat via a ChildSession bridge (removed R4).
SubAgentRun EPHEMERAL traditional sub-agent: disposable context, for exploration,
            read-only investigation, quick fanout. Dies when done; no durable identity.
Harness   executor adapter implementing AgentProcess (spawn/send/wait/terminate)
Worktree  git isolation unit, branch sweave/{task_id}/{agent}, PR via gh or REST
Integration branch  per-task branch where clean parallel work auto-merges (after the
            Stage-0 overlap check + cross-review pass); the base branch stays human-only
Manifest  JSON self-report attached to a Delegation (files touched, intent,
            confidence, breaking_change flag) — cheap intent proxy for mediation
Resolution queue  async queue consumed by the Resolution Skill (conflict mediator:
            resolve / re-queue / escalate-human); keeps the orchestrator a router,
            not a chokepoint
Memory    hindsight-backed banks: global / project-{name} / session-{id}
Router    TEMPORARY hard-edge fallback: pattern → recommended (specialist, model).
          Primary routing authority is the orchestrator LLM via `defer` tool calls
          (M1.6/M1.7); the rule-router guarantees a result when the orchestrator is
          unavailable or unsure. Roles are MODEL TIERS (models.yaml:
          orchestrator/backend/frontend/reviewer as default-model buckets), not a closed
          agent set; the rule-router resolves against specialist names.
```

### 2.1 Specialist model semantics (user-locked 2026-08-29; consistency audit same day)

- **Specialists are not sub-agents.** They persist per project with their own context.
  Current v1 behavior (fresh session per delegation) is transitional; R1 introduces
  durable context via stored `session_id` + resume (`fresh: true` per task = clean slate).
- **Context is conversational, never filesystem.** A specialist's durable context is its
  transcript + memory bank; worktrees are per-task and may be removed after merge. Each
  delegation re-injects the current worktree path; a removed worktree never invalidates
  the session or the shared serve process.
- **One active task per specialist.** Parallel work (/fanout) routes across the
  specialist pool; tasks exceeding the pool queue. "Idle/running" is therefore
  load-bearing state, and model switches queue while running.
- **Context scoping**: specialist context is per-project; the orchestrator's context is
  per-Session (a project may run several independent orchestrated conversations).
- **Deferral, not spawning.** Specialists never spawn specialists. A specialist returns a
  structured `defer{target, task}` result; only the orchestrator/runtime performs the
  spawn. One authority; mirrors Polly's supervisor-only delegation. **M1.6 ships**:
  the orchestrator calls the MCP `defer` tool (stdio subprocess), which posts to
  `/api/v2/tasks` with `parent_task_id`; the DelegationManager enforces depth 2,
  loop detect, and the 200K coordination-token budget. The MCP tool returns
  `rejected: <reason>` lines for chain-rule violations so the orchestrator's
  text-mode path can adjust.
- **DelegationManager** (R1, M1.6 shipped) enforces: depth cap (default 2), loop
  detection (A→B→A, tracked on the deferral chain it holds), per-chain
  coordination-token budget (default 200K, tiktoken cl100k_base), and records
  every deferral as a Delegation (Children tab shows the full tree). The first
  defer under a top-level delegation establishes the chain root; top-level
  delegations themselves bypass chain rules.
- **Model at request time.** Model is part of the harness contract: `send(task, model)`.
  OpenCode implements it per-message (providerID/modelID); claude/codex adapters via
  per-invocation flag/config. Switching while idle → next request; while running →
  queued for next request (never mid-request). Precedence: per-task override >
  specialist.current_model > role_ref (models.yaml) > orchestrator.default.
- **Human promotes review-done** (M1.4+M1.5, ruling 2026-08-30). A delegation
  reaching `review` stays there until a human promotes it via
  `POST /api/delegations/{id}/promote` (or clicks the Children-tab "Mark done"
  button — same endpoint). R2's cross-review automates the verdict by calling
  the same endpoint programmatically; the API is the automation seam. Extends
  the "human merges" rule to lifecycle promotion: the only path to `done` is
  this endpoint. The runtime's `success → review` transition (M1.3 step 4) is
  the half-way mark; promotion completes the cycle.
- **Runtime shape**: one shared `opencode serve` per project hosting all specialist
  sessions (HTTP), instead of one process per child run; idle specialists keep their
  session, only processes are shared.

### 2.2 Specialist creation & scope (user-locked 2026-08-29)

- **Dynamic, not fixed.** Specialists are created/edited/deleted at runtime — via UI,
  API, or YAML — scoped **per project** or **globally**. The four `sweave/agents/*`
  YAMLs are seed templates, not a closed taxonomy.
- **The orchestrator is the sole singleton.** Exactly one supervisor per project; it is
  not deletable or duplicable. Every other specialist is user-defined.
- **Storage**: global specialists in `~/.sweave/agents.yaml` (the existing dynamic-agents
  store); project specialists in `{project}/.sweave/agents.json`. Resolution order for
  a specialist name: project → global → seed templates.
- **Roles are model tiers.** `models.yaml` roles stay as default-model buckets
  (orchestrator/backend/frontend/reviewer); a specialist's `role_ref` is an optional
  hint in the resolve chain (`task_override > current_model > role_ref >
  orchestrator.default`) and may be overridden with any catalog model. Seeds are
  starter specialists the user can edit or delete.
- **Routing authority (amended 2026-08-29)**: the orchestrator decides which specialist
  receives a task via its own LLM `defer` tool call. The rule-router is a temporary
  hard-edge fallback returning a *recommended primary*, not a hard decision.
- **Child runs = traditional sub-agents**: ephemeral, used for exploration/investigation
  (Polly's `/investigate` pattern); no worktree or PR by default. Delegation of
  *implementation* work goes to specialists in worktrees; delegation of *read* work
  goes to sub-agent runs.

### 2.3 Memory openness (user-locked 2026-08-29)

The memory layer decomposes into three independently configurable parts; users pick
per part in config.yaml + Settings:
1. **Embedder** — where vectors come from: Workers AI (`@cf/baai/*`), Ollama (local,
   free, private), OpenAI, or a custom endpoint.
2. **Vector store** — where they live: hindsight embedded (default: local-first,
   private), **Cloudflare Vectorize** (free tier is sufficient: 30M queried + 5M stored
   dims/mo; REST via httpx, zero new deps; `namespace` == our bank hierarchy), Qdrant /
   sqlite-vec later. Hindsight docker/cloud remain as whole-stack alternatives.
3. **Logic layer** — extraction/reflect/compaction: hindsight-style, rule-based v1,
   mem0-inspired single-pass ADD-only extraction + Zep-style temporal validity later (R6).
Trade-off recorded: cloud stores are not local-first (privacy + latency) and are
eventually consistent — local embedded stays the default; Vectorize is opt-in.

## 3. Architecture

```
┌──────────┐   ┌─────────────────────────────────────────────┐
│ CLI      │   │ Web SPA (vanilla JS, no build)              │
│ sweave … │   │ chat · children · agents · memory · settings│
└────┬─────┘   └────────────────────┬────────────────────────┘
     │ typer                        │ REST + WS (/ws)
     ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI  sweave/web/server.py                               │
├─────────────────────────────────────────────────────────────┤
│ Services (internal, sweave/tools + sweave/*):               │
│   RuleRouter      pattern→agent/model   ✅ (llm fallback ⚠️)│
│   DelegateTaskTool worktree+spec+spawn  ✅                  │
│   WorktreeManager git wt + PR (gh/REST) ✅                  │
│   MemoryTool      recall/retain/reflect ✅ (hindsight)     │
│   ProjectManager  projects/sessions/msgs✅ (JSON store)     │
├─────────────────────────────────────────────────────────────┤
│ Harness layer (sweave/harness)                              │
│   AgentSpec → Harness.spawn → AgentProcess(send/wait/term)  │
│   OpenCode: `opencode serve --port 0` + HTTP  ✅            │
│   claude/codex: detect-only 📐 (no spawn)                   │
└─────────────────────────────────────────────────────────────┘
```

Data flow of a delegated task (today, real):
`POST /api/tasks` → RuleRouter.route → DelegateTaskTool.execute →
WorktreeManager.create_worktree → AgentSpec(prompt, model, worktree) →
OpenCodeHarness.spawn (`opencode serve`, cwd=worktree) → HTTP message → result string.

## 4. Component status (audit 2026-08-29, post-M1.prep)

| Component | Status | Notes |
|---|---|---|
| WorktreeManager (create/remove/list/PR) | ✅ | server, CLI, delegate all call it |
| RuleRouter matching + `{{templates}}` | ✅ | first-match regex/keyword |
| RuleRouter `_llm_fallback` | ⚠️ | keyword heuristic, no LLM call |
| Task delegation → opencode serve | ✅ | real subprocess + HTTP session |
| Agent lifecycle (wait/terminate/attach) | ✅ | M1.3 + M1.4+M1.5 — wait returns the final Delegation; terminate kills the per-specialist serve on idle TTL; attach delegates to SpecialistRuntime; `_active_agents` removed (M1.4+M1.5 step 0 — the registry owns the runners; dead dict audited + deleted) |
| Chat message endpoint | ✅ | M1.7 — `POST /api/sessions/{id}/messages` (user role) drives the orchestrator chat loop: persist user message, run the orchestrator specialist via SpecialistRuntime with the Session-bound session-id callbacks (M1.7 step 1), persist the assistant reply. Per-session asyncio.Lock for serial conversation semantics. Non-user roles (system/tool/assistant) keep the persist-only contract. Orchestrator-unreachable or timeout: explicit error message persisted, never a silent fallback. |
| Agent definitions `sweave/agents/*/config.yaml` | ✅ | loader wired (R0): prompts/tools/harness from YAML, FALLBACK_PROMPTS for gaps; model_template stored for R1 |
| Claude Code / Codex harnesses | 📐 | detect-only (which/--version), no spawn |
| `/ws` realtime | ✅ | `webspaces` dep; WSEventBus + unified vocabulary landed in M1.prep; legacy event names preserved |
| OpenCode spawn path | ✅ | M1.0 + M1.3 + M1.4+M1.5 — exe resolution + log-file port discovery + v2 API (`/session`, `parts` body, per-message model, chunked-stream read); SpecialistRuntime wraps one opencode serve per (specialist, worktree) with idle TTL; model path uses structured ModelRef (K-revised) so multi-provider configs (ollama, gmi/gmicloud, zai, opencode default) all route correctly; **M1.4+M1.5 step 1** promotes `ModelRef` + `model_ref_to_wire` into `harness/base.py` (the contract type) and `Message` gains `model: ModelRef \| None` (per-message beats spawn-time) |
| `sweave doctor`, `models`, `rules`, `route` | ✅ | `models --reset` ⚠️ stub |
| Web UI v1 | ✅ | 40/40; welcome-mode gating fixed; see test_sidebar_nav.js |
| **Server split into routers/** | ✅ | M1.prep — no import-time singletons, FastAPI lifespan owns AppState |
| **Atomic JSON + per-project locks** | ✅ | M1.prep — `runtime/locking.py`; ProjectManager routes all writes through |
| **JobRunner + Delegation store** | ✅ | M1.prep — `runtime/job_runner.py`; `POST /api/v2/tasks` returns `{delegation_id, status}` |
| **SpecialistRuntime (per-specialist ServeRunner + session reuse)** | ✅ | M1.3 — `runtime/specialist_runtime.py` orchestrates one delegation: resolves a ServeRunner (lazy start per (specialist, worktree)), ensures a session (create, recreate on 404, or reuse), sends a single message with structured ModelRef in `body["model"]`. M1.3 step 4 wires a per-turn timeout (default 15 min) and routes success to `review` (M1.4 promotes to `done`). Orphan sweep in `serve_runner.find_orphan_serves` cleans up stale processes on server boot |
| **Per-delegation trace log (JSONL)** | ✅ | M1.prep — `~/.sweave/traces/{id}.jsonl` |
| **Specialist store + /api/specialists CRUD** | ✅ | M1.2 — `runtime/specialist_store.py` (global `~/.sweave/agents.yaml` + per-project `{project}/.sweave/agents.json`); resolution project→global→seed; `is_orchestrator` flag for the per-project singleton; model precedence chain at submit; `PUT /api/specialists/{name}/model` emits `model.changed {name, model, scope}`; `specialist.created/updated/deleted` events; override log at `{project}/.sweave/override_log.jsonl` (global fallback `~/.sweave/override_log.jsonl`) |
| **/api/agents bridge + render fix** | ✅ | M1.2 — returns `{builtin, global, dynamic}` arrays (the M1.prep dicts were the root cause of the empty Agents tab); description-overwrite bug fixed; routes writes through the specialist store; orchestrator name 409 on create/delete |
| **Delegation v2 schema + per-project persistence** | ✅ | M1.1 — schema_version=2 (worktree, branch, pr_url, parent_task_id, manifest); per-project `{project}/.sweave/delegations.json` via `PerProjectDelegationStores`; v1→v2 migration in `from_dict` |
| **SubAgentRun (ephemeral, capped)** | ✅ | M1.1 — `runtime/subagent_store.py`; per-process, in-memory, FIFO-capped at 500; serves R2's `/investigate` |
| **UI v1 compat bridge (ChildSession)** | ✅ | M1.1 — `ChildSession.delegation_id` field + JobRunner bridge write on submit; R4 removes the bridge |
| **DelegationManager (depth / loop / budget)** | ✅ | M1.6 — `runtime/delegation_manager.py`; per-process gate; depth cap (default 2), loop detect (per-chain active set), coordination-token budget (default 200K, tiktoken cl100k_base estimate). `ChainError` subclasses (`DepthExceededError` / `LoopDetectedError` / `BudgetExceededError`) all raise 409 with a "rejected: <reason>" surface for the MCP `defer` tool. Top-level delegations bypass chain rules (no parent = no chain); first defer establishes the chain root |
| **Sweave MCP server (stdio, defer + list_specialists)** | ✅ | M1.6 — `sweave/mcp/` package; `python -m sweave.mcp`; official `mcp` SDK (MIT, §8); two tools: `defer(target, task, reason, caller_delegation_id)` posts to `/api/v2/tasks` with `parent_task_id` and surfaces the DelegationManager's "rejected: ..." lines as plain text the orchestrator can act on; `list_specialists()` returns the resolved pool (excludes the orchestrator singleton). Shared token at `~/.sweave/mcp_token` auto-generated; localhost-only auth via `X-Sweave-MCP-Token` |
| **Per-project opencode.json plumbing** | ✅ | M1.6 — `runtime/mcp_config.py`; `ensure_mcp_config(project_dir)` is idempotent (the `_sweave_managed` marker; user-edited blocks are preserved); triggered on `POST /api/projects/{name}/active` so the orchestrator's serve cwd sees the sweave MCP server automatically |
| **Orchestrator defer tool contract** | ✅ | M1.6 — `sweave/agents/orchestrator/config.yaml` prompt contains the defer tool spec (args, return shapes, "rejected:" / "error:" / "queued:"); "Never implement code yourself" is the headline rule |
| **Parent gating (tree lifecycle)** | ✅ | M1.6 — `JobRunner._wait_for_children` blocks the parent's `review` transition until every child delegation reaches `done` or `failed`; bounded by `turn_timeout` so a wedged child can't stall the parent. Trace records `children_settled` (count, done, failed) or `children_settle_timeout`. **Synthesis generation (re-prompting the orchestrator with child results) is M1.7 scope** — M1.6 delivers tree lifecycle + gating only; M1.7 step 3 delivers the synthesis loop for the chat path (server-composed prompt, server-built children summary, second orchestrator turn) |
| **Chat loop (orchestrator conversation)** | ✅ | M1.7 — `sweave/chat/loop.py` ChatLoop drives `/api/sessions/{id}/messages` for user messages: per-session asyncio.Lock for serial semantics, builds a chat Delegation (kind=chat, depth=0, no worktree), runs the orchestrator specialist via SpecialistRuntime with Session-bound session-id callbacks (M1.7 step 1), persists the assistant reply. **Auto-`done` on success** (M1.7 step 3; implementation children still stop at `review`). Orchestrator-unreachable or timeout: explicit error message persisted, never a silent fallback. Concurrent calls on different sessions run in parallel. |
| **Synthesis loop (chat + children → second turn)** | ✅ | M1.7 — after the orchestrator's first turn, ChatLoop scans for child delegations (parent_task_id == chat_d.delegation_id). With children: bounded wait, server-composed synthesis prompt (per-child {specialist, task, status, output, error}, oldest-first truncation, ~8K token cap, tiktoken heuristic), second orchestrator turn, final assistant message. Without children: fast path, first turn's reply is the final answer. Children failing: synthesis still runs with the failure noted. |
| **Runtime transcript system (per-turn composed prompt)** | ✅ | M1.7 — `sweave/chat/transcript.py`; the runtime owns the per-turn composed prompt (the LLM is a consumer of what the runtime built). Sections: curated memory (top-k=5, ~2K), multi-source "what's new" (memory entries with ts > last_recall_ts + git diff since last_snapshot, ~1K), synthesis (when children), one-paragraph transcript reference (~100), user message. The composed prompt size is O(memory + synthesis + user), NOT O(transcript_length) — long conversations don't bloat the per-turn prompt. Trace records per-section sizes + dropped counts. |
| **Per-Session orchestrator binding** | ✅ | M1.7 — Session gains `orchestrator_session_id: str | None` (M1.7 step 1); the durable opencode session id lives on the Session record, NOT on the Specialist record. Three Sweave sessions of the same project get three independent orchestrator contexts. Legacy session files (no `orchestrator_session_id`) load with `None`; the next turn's runtime call creates the binding. `SpecialistRuntime.run` + `_ensure_session` accept `session_id_getter` / `session_id_setter` callbacks; default is the M1.3 Specialist-record behaviour. |
| **Delegation v3 schema (chain metadata)** | ✅ | M1.6 — schema_version=3; new fields `depth` (0 for orchestrator, +1 per defer), `chain_root_id` (None for top-level; the root of the deferral chain otherwise), `coordination_tokens` (tiktoken estimate; coordination traffic only — specialist internal work is opaque by design). `from_dict` migrates v2 → v3 and v1 → v3 |
| **Human promotion (review → done) endpoint** | ✅ | M1.4+M1.5 — `POST /api/delegations/{id}/promote`; 409 from non-review; 404 unknown; trace `status_changed` (source=human_promote); WS `delegation.status_changed`; bridged `ChildSession.status` synced to `done`; Children-tab "Mark done" button (review only, offsetParent-verifiable). **R2's cross-review calls this same endpoint programmatically** — the API is the automation seam |
| **pytest suite** | ✅ | M1.prep + M1.0 + M1.1 + M1.2 + M1.3 + M1.4+M1.5 — 295 tests across 27 files; `pytest` is the source of truth |
| Git history | ✅ | M1.prep + M1.0 + M1.1 + M1.2 + M1.3 + M1.4+M1.5 — 24 commits; `docs/M1_PREP_PLAN.md` ... `docs/M1_4_5_PLAN.md` are the plans of record |

## 5. Locked decisions

1. **Standalone, Polly-style** — no omnigent dependency (remove `omnigent[hindsight]` from
   pyproject). Keep Omnigent agent-YAML *shape* as our agent spec.
2. **OpenCode first** — the only spawn-capable harness for milestone 1; claude/codex
   adapters are documented roadmap (R3), not day-one code.
3. **Docs split** — this file = architecture/design; AGENTS.md = how agents work in this
   repo; PROJECT_STATE.md = runtime state + session history.
4. Vanilla-JS no-build SPA; FastAPI serves static + REST + WS.
5. Windows-first (detached servers via scripts, backend-driven file browser).

## 6. Roadmap

### R0 — Hygiene (before any feature) — ✅ done 2026-08-29
- **First git commit** (the design was lost once; unversioned = lost again). ✅
- Agent YAML loader ✅ (`sweave/agents/loader.py` parses `agents/*/config.yaml`,
  replaces hardcoded prompts in DelegateTaskTool; FALLBACK_PROMPTS kept for missing
  fields; gated by test_agents_loader.py).
- Fix `/ws` 404 ✅ (root cause: uvicorn had no websocket implementation; added
  `websockets>=13` dep). Fix CLI bug `router.router._llm_fallback` ✅.
- Remove dead deps ✅ (`omnigent`, `asyncio-mqtt`, `pygit2`, `python-dotenv` — none
  imported anywhere).
- **Harness spawn repair** ✅ code / ⚠️ live-verify pending: (1) `.cmd` shim → resolves
  the npm shim's target `opencode.exe` (`_resolve_command`). (2) replaced broken
  `to_thread(readline)` port discovery with log-file polling (`_wait_for_server_ready`
  reads a serve log file, no pipes). (3) serve stdout/stderr redirected to a temp log
  file (pipe-held output triggered the Bun illegal-instruction crash + freeze).
  Live serve verification (session-resume, per-message model) deferred to a safe
  launch window; M1 runtime must feature-detect resume at runtime.

### R1 — Specialist runtime + agent lifecycle (make delegation trustworthy)
Planned as 10 gated steps (~11 sessions incl. prep + streaming); critical chain
M1.0→M1.3→M1.4/5→M1.6→M1.7.
- **M1.prep Backend foundations** — ✅ done 2026-08-29. split server.py into
  router modules + FastAPI lifespan app state (no import-time singletons);
  in-process asyncio JobRunner for delegations (submit → delegation id → poll/WS
  status; no broker — Delegation records ARE the queue, per §8 decision);
  atomic JSON writes + per-project lock; unified `/ws` event vocabulary
  (`delegation.status_changed`, `model.changed`, legacy names preserved);
  structured per-delegation JSONL trace logs at `~/.sweave/traces/{id}.jsonl`;
  `tests/` pytest skeleton with ports of the logic-test scripts. 62 pytest
  tests, 13 run.py --check, 40 test_full.py, 8 test_browser.py, 8 test_projects
  endpoints all green. **Sync `POST /api/tasks` kept for backward compat; new
  async path is `POST /api/v2/tasks` (returns `{delegation_id, status: queued}`)**.
- **M1.0 Live serve probe** — ◐ mostly done 2026-08-29: real API shape discovered and
  implemented (v2 `/session` + `/session/{id}/message`; legacy `/api/session` now serves
  web UI HTML; body `parts[].text`; per-message `{providerID, modelID}`; chunked JSON
  stream responses; provider errors surface verbatim in traces). **Still open** (decides
  M1.3 shape): session resume across serve **restarts**, completion signal semantics.
- **M1.1 Record split** — ✅ done 2026-08-29. Delegation v2 schema (`worktree_path`,
  `branch`, `pr_url`, `parent_task_id`, `manifest`; schema_version=2; v1→v2
  migration in `from_dict`); per-project disk persistence
  (`{project}/.sweave/delegations.json`, atomic write-through via
  `runtime.locking.atomic_write_json_sync`); `SubAgentRun` ephemeral
  type (`runtime/subagent_store.py`, in-memory, FIFO-capped at 500);
  API filters on `/api/delegations` (`?project_name=&status=&parent_task_id=`)
  and v2 task accepts `parent_task_id` + `manifest` passthrough; UI v1
  compat bridge writes a `ChildSession` carrying `delegation_id` on
  submit (R4 removes the bridge); `SubAgentRun` endpoints
  (`POST/GET/finish`); 122 pytest across 14 files, 13/13 run.py --check,
  40/40 test_full, 8/8 test_browser, ALL GREEN test_agents_loader.
- **M1.2 Specialist store + CRUD** — ✅ done 2026-08-29. `Specialist` persisted
  per scope (global `~/.sweave/agents.yaml` — home-anchored, replacing the
  M1.prep CWD-relative `agents.yaml`; per-project `{project}/.sweave/agents.json`);
  resolution project → global → seed views (orchestrator name excluded from the
  general resolve path; the singleton is resolved via `resolve_orchestrator()`).
  `is_orchestrator: bool` flag on Specialist gates create/delete (409) and the
  model-picker exclusion. Model precedence chain: `task_override >
  specialist.current_model > config.resolve_model(role_ref) > legacy
  config.resolve_model(agent)`; `role_ref` is an optional hint (unknown falls
  through to the orchestrator default). `/api/specialists` CRUD + `PUT /{name}/model`
  (emits `model.changed` with the new `{name, model, scope}` shape) +
  `specialist.created|updated|deleted` events. Override log at
  `{project}/.sweave/override_log.jsonl` (global fallback at
  `~/.sweave/override_log.jsonl`) appended when a v2 task carries an explicit
  `agent` that differs from the router decision (R6 dispatch gold labels).
  `/api/agents` bridge returns `{builtin, global, dynamic}` (arrays now — fixes
  the M1.prep render bug where dicts + `dict.map` rendered the Agents tab empty)
  + the description-overwrite bug (PUT now patches `description` and
  `system_prompt` independently). **Tier framing baked in**: Orchestrator is
  the per-project singleton (flag on Specialist, not a separate type); Specialist
  is persistent, named, user-creatable; SubAgent is the M1.1 ephemeral
  `SubAgentRun` (out of scope here). 207 pytest across 20 files; 13/13
  run.py --check; 40/40 test_full; 8/8 test_browser; ALL GREEN test_agents_loader.
- **M1.3 Shared serve + durable context** — ✅ done 2026-08-30. `ServeRunner`
  per (specialist, worktree), lazy start, idle TTL (default 30 min,
  config knob), psutil orphan sweep. `SpecialistRuntime` per delegation
  ensures a session (create, recreate-on-404, or reuse), sends one
  message with structured ModelRef in `body["model"]` (M1.3 K-revised;
  the user's `opencode.json` declares multi-provider: ollama,
  gmicloud, zai, opencode default — bare-name fallback with warning
  for v1 records). `JobRunner` integrates the runtime (legacy
  `delegate_tool.execute` path preserved when no runtime is wired).
  Stuck detection v1: per-turn timeout (default 15 min) on the
  runtime + legacy paths; on expiry the delegation is marked failed
  with an explicit error and the ServeRunner is recycled on next use.
  M1.3 step 4 routes success to `review` (M1.4 promotes to `done`).
  **Note** (M1.3 post-step-0 amendment): the original probe-4 finding
  ("sessions are in-memory only") was misleading — the probe's
  `_snapshot_storage` saw the pre-existing `opencode.db` file
  before and after, correctly, but missed that a *new row* was
  inserted into the existing SQLite table. Confirmed by direct
  query: opencode persists sessions in
  `~/.local/share/opencode/opencode.db`; sessions DO survive
  opencode process restarts (the SQLite DB is persistent; the
  `opencode serve` is just the in-memory request handler). The
  stored `session_id` is therefore useful across restarts, not
  just within one ServeRunner's lifetime — Branch A's cwd
  isolation is still the architectural rationale for per-specialist
  runners, but the cross-restart session reuse is a bonus on top.
  269/269 pytest across 25 files; 13/13 run.py --check; 40/40
  test_full; 8/8 test_browser; ALL GREEN test_agents_loader. Live
  gate against a real opencode + provider is documented in
  `tests/test_m1_3_step5_live_gate.py` (run by hand when a working
  opencode + provider is available — the probe results doc is the
  manual proof for now).
- **M1.4 Lifecycle completion** — ✅ done 2026-09-03 (folded into M1.5 —
  `docs/M1_4_5_PLAN.md`, ruling 2026-08-30). M1.3 pre-delivered completion
  detection, stuck detection v1 (turn timeout), real attach, serve
  lifecycle. This plan delivered: **hermetic flake fix** (M1.3 step 3
  runtime tests now set `SWEAVE_MOCK_OPENCODE=1` via a module-scoped
  autouse fixture — the real-subprocess leak source is gone, pinned by
  `test_runtime_runner_is_mocked_no_real_subprocess`), **`_active_agents`
  audit** (removed; M1.3's `ServeRunnerRegistry` owns process
  lifecycle — grep confirmed no external consumers), and **human
  promotes review-done** (new `POST /api/delegations/{id}/promote`
  endpoint — only valid from `review`; Children-tab "Mark done"
  button visible only for `review` records; bridged `ChildSession`
  status synced to `done` so the UI re-renders; **R2 cross-review
  automates via the same endpoint**). Live spot-check: 3 delegations
  to one specialist all reached `review`, one promoted via API, no
  process leaks. 295 pytest across 27 files (was 269; +26); 13/13
  `run.py --check`; 40/40 `test_full`; suite 3× consecutive green.
- **M1.5 Model at request time** — ✅ done 2026-09-03 (merged with
  M1.4, `docs/M1_4_5_PLAN.md`). `ModelRef` + `model_ref_to_wire`
  promoted to the harness contract (`harness/base.py`); `Message`
  gains optional `model: ModelRef | None`; `OpenCodeProcess.send`
  prefers `message.model` over `spec.model` (per-message beats
  spawn-time). `specialist_store` re-exports for backward
  compatibility (no API break). Switch semantics enforced and tested:
  `PUT /api/specialists/{name}/model` accepts + stores mid-task; the
  switch applies to the NEXT delegation (submit-time resolution
  makes this structural), never mid-task. 4-level chain verified end-
  to-end incl. `orchestrator.default` final fallback. Trace records
  `model_used` on every delegation completion (source:
  task_override / specialist.current_model / none) — useful for
  debugging switch semantics and R6 dispatch eval. 295 pytest, 13/13
  `run.py --check`, 40/40 `test_full` green.
  Cost-budget enforcement: local tiktoken estimates by default;
  **Cloudflare AI Gateway** as opt-in native enforcement for cloud
  providers (§8 map).
- **M1.6 DelegationManager + deferral** (~2, planned in detail:
  `docs/M1_6_PLAN.md`): **defer = real MCP tool** (ruling 2026-08-30 — no JSON
  parsing): `sweave/mcp` stdio server exposes `defer(target, task)` +
  `list_specialists()` to the orchestrator's opencode session; DelegationManager
  enforces depth 2, loop detection on the chain, 200K-token coordination budget
  (specialist internal work excluded — cap targets runaway coordination, not work).
  Delegation v3 (depth, chain_root_id, coordination_tokens) + parent gates on
  children before review. **Branch notes** (vs. plan): opencode MCP
  discovery was verified at step 0 to use per-project `opencode.json`
  with `type: "local"` + `command: [array]` + `environment: {KEY: VALUE}`
  + `timeout: 30000` (no global-config injection fallback needed).
  The MCP `defer` tool posts to `/api/v2/tasks` (no MCP-specific URL);
  the DelegationManager runs inside that handler. The MCP server is
  the **only** consumer of the per-project opencode.json plumbing --
  specialists stay tool-clean. The first defer establishes the chain
  root; top-level delegations (no parent) bypass chain rules. The
  per-project opencode.json write is idempotent (the
  `_sweave_managed` marker protects user-edited blocks). Live
  mini-scene (gmi, tiny): a parent + a defer child end-to-end with
  loop probe (third defer to the same target is rejected with 409
  "rejected: loop detected"). 336/336 pytest (was 295; +41); 13/13
  run.py --check; 40/40 test_full; suite 3x consecutive green.
- **M1.7 Orchestrator chat loop** (~2.2, **done 2026-09-04** per `docs/M1_7_PLAN.md`):
  fixes the per-Session context wrinkle (orchestrator's durable opencode session
  binding moves to the **Session record**, one per project.session; sessions gain
  schema_version + migration); `POST /messages` spawns chat-turn Delegations
  (kind=chat, auto-`done` — review stays for implementation diffs); serial per-session
  queue; **synthesis loop** (children terminal → capped summaries → orchestrator
  re-prompt → persisted assistant reply); **runtime transcript system** (the runtime
  owns the per-turn composed prompt — curated memory, multi-source "what's new"
  with timestamps, one-paragraph transcript reference; the LLM is a consumer of
  what the runtime built); orchestrator-unreachable = explicit chat error, never
  silent fallback. Gate: live (`scripts/m1_7_live_scene.py`) — defer tree +
  synthesized answer in Chat + per-Session binding proven on disk + composed_prompt
  event on the trace.
- **M1.8 Streaming** (~1): orchestrator chat + specialist output streamed over `/ws`
  (SSE fallback); delegation progress events from M1.prep's event vocabulary. Gate:
  chat replies render incrementally.
- **M1.9 Dogfood pass** (~0.5-1, added 2026-09-04): minimal daily-driver polish before
  any feature work resumes - Children tab live tree status (WS events), per-delegation
  detail view reading its JSONL trace, promote buttons on every review record, chat
  streaming polish. Purpose: warm-up + **the user daily-drives Sweave on real work**;
  the friction list becomes the requirements input for R4 (UI v2). Gate: the user
  completes one real task end-to-end through the UI and files the friction list.
- M1 exit demo: chat → orchestrator delegates → specialist worktree diff reaches review;
  follow-up chat shows durable specialist context; model switched while idle between
  tasks.

### R2 — Orchestrator skills (Polly's core loop)
- `/fanout`: parallel-safe subtasks → routed across the specialist pool (one Delegation,
  worktree and PR each; overflow queues — §2.1). Merge handling: **Stage-0 heuristic**
  (path-overlap check, 0 tokens) → clean work auto-merges into the per-task **integration
  branch** (principle 4 exception); overlapping work goes to the **resolution queue**.
- `/cross-review`: implementer's diff → *different-role* reviewer Delegation; blocking
  issues loop back as fixes. (Same-vendor rule becomes: reviewer model ≠ implementer
  model, later different harness.) Cross-review is also the semantic-conflict layer Git
  cannot see (e.g. frontend calls an API backend didn't add).
- `/investigate` - **DEMOTED 2026-09-04 (user challenge accepted)**: opencode's
  native `task` tool already spawns subagents for generic exploration inside a
  specialist's own session, so a separate Sweave skill adds little today. Revisit
  **when the custom agent runtime exists (R6+)** - there `/investigate` becomes a
  first-class runtime capability with Sweave-side visibility (SubAgentRun tracking),
  cross-session memory retention of findings, and parallel codebase-wide synthesis.
  The M1.1 `SubAgentRun` machinery stays as infrastructure until then.
- Skills = skills/{name}/SKILL.md conventions + delegation presets; human merges main,
  always.
- **Resolution queue** (from the 2026-08-29 architecture discussion): async queue with
  diff3 + both manifests as payload; the Resolution Skill consumes it asynchronously
  (resolve / re-queue / escalate-human). v1 consumer = rule-based + reviewer Delegation;
  the small-model consumer is R6 scope.

### R3 — Multi-harness
- `ClaudeCodeHarness`, `CodexHarness` implementing AgentProcess (subprocess/headless),
  register in harness_registry; per-agent `harness:` field already in AgentSpec.
- Cross-vendor review then = reviewer on a different harness than implementer.

### R4 — Web UI v2 (folded from UI_PLAN.md)
- Phase 1: statusline topbar (project + visible CWD), session dropdown w/ search,
  Ctrl+K palette, `GET /api/models/catalog` (live harness models + models.dev cache).
- Phase 2: model combobox grouped by provider with live/catalog badges.
- Phase 3: agents workbench (two-pane, specialists grouped by scope with idle/running
  status, inline model switch on idle specialists, ▶ Run → child, activity, pulse via /ws).
- Regression harness: Playwright+Edge scripts (see test_sidebar_nav.js pattern).

### R5 — Packaging
- `pipx install sweave`, versioned releases, first public README pass.
- **Remote access (optional add-on)**: `cloudflared` Tunnel + Access in front of the
  local server — use your Sweave UI from anywhere with zero-trust auth, no port
  forwarding (§8 Cloudflare map). Config-gated, off by default.
- **Cross-platform verification**: CI or manual pass on macOS + Linux (Windows-first
  until here; start/stop scripts, paths, process handling are the risk spots).
- **Escalation UX v1**: human resolution flow for the R2 resolution queue — see the
  diff3 + manifests, choose resolve / re-queue / escalate, resolution recorded as a
  gold label (feeds R6 dispatch training).

### R6 — Local orchestrator thesis (📐 future bet — the differentiator)Gated on M1–R2 stability and real task volume. From the 2026-08-29 architecture
discussion; cheap model as PM, strong models as engineers.
- **Shared-backbone encoder heads** (~150MB, 20–50ms CPU): intent (task↔specialist
  matching), dispatch, resolution (merge_auto / defer / split / escalate), mediation
  (trivial vs needs-review) — heads on ONE backbone (bge-small / jina-v2-small-code
  class), never separate models. No separate encoder instances (RAM fragmentation).
- **Decoder fallback** (1.5–3B, e.g. Qwen2.5-Coder) only for generative outputs
  (subtask descriptions, complex deferrals), grammar-constrained (GBNF).
- **Dispatch bootstrapping** (the hard problem): hand rules → encoder mimicry →
  strong-model distillation ($20–50 oracle runs) → self-play simulation → active
  learning from logged routing overrides (collected since M1.2).
- **Resolution Skill v2**: small-model consumer of the resolution queue; escalation UX
  (what the human sees on `escalate_human`, and how their resolution feeds training).
- **Memory compaction cadence**: raw → RAG immediately; episodic compaction (hourly/
  per-task event); weekly project-lore extraction. Rule: compact the why, keep paths
  and signatures verbatim (over-compaction trap).
- **Orchestrator scope decision** (open): does the orchestrator get its own RAG over
  project   docs/manifests (markdown-only knowledge), vs re-reading session state?
  Latency question (local orchestrator + cloud specialists) also lands here.

### R7 — Memory openness (pluggable embedder + store)
Decomposition per §2.3. First increment (after M1, before/parallel with R4):
- Refactor `MemoryBackend` into composable parts: `Embedder` + `VectorStore` + logic
  layer; `MemoryFactory` maps config → composition; hindsight stays default.
- **CloudflareVectorizeMemory** (user-requested): httpx-only REST backend
  (`/vectorize/v2/indexes/{index}/insert|query`), one index per project,
  `namespace` = bank scope (global / project-{name} / session-{id}), metadata
  filtering needs pre-declared metadata indexes (bank, agent, timestamp);
  embeddings via Workers AI REST or Ollama (local, free).
- Settings UI (R4 memory pane) exposes the three choices with a health check per part.
- Later stores: Qdrant, sqlite-vec (fully local alternative).

## 7. Risks / open questions
- OpenCode serve port discovery (probe 4096-4199) is fragile — revisit with `--port 0`
  stdout parsing only.
- No completion tracking yet: "task done" currently = HTTP response received (R1 risk).
- JSON-file storage for projects/sessions is fine single-user; SQLite migration deferred.
- models.dev catalog cache staleness → refresh endpoint in R4.
- Windows subprocess lifecycle (orphaned opencode.exe on crash) — add PID sweep in R1.

## 8. Third-party stack (policy + candidates)

Policy (user-locked 2026-08-29): open to third-party libraries for help **and
inspiration**. Prefer stdlib + small maintained libs; permissive licenses only
(Apache-2.0/MIT/BSD); avoid heavy frameworks that would own the orchestration layer
(Celery/LangChain/Letta runtimes — Sweave IS the orchestrator; borrow patterns, not
runtimes). Every adoption gets recorded here.

### Adopt now
| Need | Library | Why | Where |
|---|---|---|---|
| Orphan process sweep | `psutil` | find/kill process trees by cmdline reliably on Windows | M1.3 |
| Chain cost budgets | `tiktoken` | token counting for per-chain budget enforcement (approximate for non-OpenAI BPE — fine for budgets) | M1.6 |
| 3-way merge simulation | `merge3` | diff3 merge without touching git — resolution-queue payload + Stage-0 overlap checks | R2 |
| Harness tests w/o live opencode | `respx` | httpx mocking; test spawn/send logic deterministically | M1 tests |
| MCP server SDK | `mcp` (modelcontextprotocol python SDK) | official SDK for the sweave defer/list_specialists stdio server the orchestrator's opencode session calls (M1.6) — MIT, small, no loop ownership | M1.6 |

### Deliberately NOT adopted (and why)
| Category | Candidates | Why not |
|---|---|---|
| Broker task queues | Taskiq, Streaq, Celery, Dramatiq, ARQ | all need Redis/RabbitMQ — wrong weight for local-first single-user; ARQ maintenance-only. Delegation records + in-process asyncio runner ARE the queue (M1.prep decision) |
| Agent runtimes | Letta/MemGPT, CrewAI, AutoGen, LangGraph | they own the agent loop; Sweave's value is owning it. Inspiration only |
| Memory frameworks | mem0, Zep, Letta | hindsight already integrated behind `MemoryBackend` protocol. **Borrow**: mem0's single-pass ADD-only extraction + multi-signal retrieval fusion; Zep's temporal validity (`valid_at`/`invalid_at`) for project lore |
| Git libraries | GitPython, pygit2 | subprocess porcelain parsing is simple and dependency-free |

### Adopt later (R6 scope)
- Encoders: `sentence-transformers` / `FlagEmbedding` (bge-class shared backbone),
  `onnxruntime` for 20–50ms CPU inference
- Grammar-constrained decoding: llama.cpp GBNF / `outlines` (1.5–3B decoder fallback)
- Compaction scheduler: `APScheduler` (hourly/per-task-event cadence) — or plain asyncio
  task if one-shot suffices
- Model cost map: models.dev metadata (already planned via catalog API); litellm's
  registry as inspiration only

### Adopt later (user-requested, R7)
- **Cloudflare Vectorize** as pluggable vector store (REST via existing httpx — zero new
  deps; free tier sufficient for our scale; `namespace` == bank hierarchy; eventually
  consistent inserts; not local-first → opt-in only). Embeddings: Workers AI REST or
  Ollama. See §2.3 + R7.

### Cloudflare platform map (survey 2026-08-29, user-initiated)
All Cloudflare, all opt-in (local-first principle holds); single CF account covers all.
| Product | Utility to Sweave | Verdict |
|---|---|---|
| AI Gateway | Proxy before any provider: cost analytics (GraphQL), response caching, spend budgets w/ auto-block, retries + model fallback, BYOK. Core features free; provider costs pass through unmarked. Integrates by pointing opencode provider base URLs at it (custom endpoints already in use) | **Adopt M1.6/R2** — native enforcement for chain budgets + per-project spend in UI v2 |
| Vectorize | R7 vector store (see above) | ✅ Adopted |
| Workers AI | Embeddings for Vectorize; reranking; 10K neurons/day free | Adopt in R7 |
| AI Search (ex-AutoRAG) | Managed RAG over R2/sites; /search + /chat + built-in MCP endpoint; hybrid retrieval; free in beta | Future R6 — orchestrator project-docs knowledge (open question Q3) |
| Tunnel + Access | Outbound tunnel + zero-trust auth → remote access to the local UI, no port forwarding | R5+ candidate ("remote access") |
| R2 | Raw memory text backing store beside Vectorize vectors; AI Search source | R7 ext |
| Agents SDK | Stateful-agent framework on Durable Objects | Skip — owns the loop (policy); pattern inspiration (HITL, MCP) |
| Workflows / DO / Queues | Durable execution semantics | Inspiration for DelegationManager durability only |
| Browser Run / Containers / Hyperdrive / KV | — | Skip for now |
- **Native candidates (C++, only if pain shows up — user speciality)**: (1) process
  supervisor via Windows Job Objects (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` per serve)
  replaces the Python orphan sweep properly; (2) high-frequency serve-log watcher
  (overlapped I/O) if N-serves-per-project ever materializes. Small bounded helper exes
  (`sweave-supervisor`) behind a named-pipe/stdio protocol; Python host stays.
