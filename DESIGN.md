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
            The deferral tree and /fanout nodes are Delegations. v1 code calls these
            ChildSession — R1 renames.
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
Router    pattern → (specialist, model) decision; roles are MODEL TIERS (models.yaml:
          orchestrator/backend/frontend/reviewer as default model buckets), not a closed
          agent set; rules target specialist names.
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
  spawn. One authority; mirrors Polly's supervisor-only delegation.
- **DelegationManager** (R1) enforces: depth cap (default 2), loop detection (A→B→A,
  tracked on the deferral chain it holds), per-chain cost budget, and records every
  deferral as a Delegation (Children tab shows the full tree).
- **Model at request time.** Model is part of the harness contract: `send(task, model)`.
  OpenCode implements it per-message (providerID/modelID); claude/codex adapters via
  per-invocation flag/config. Switching while idle → next request; while running →
  queued for next request (never mid-request). Precedence: per-task override >
  specialist.current_model > role default (models.yaml).
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
  (orchestrator/backend/frontend/reviewer); a specialist references a role for its
  default model and may override with any catalog model. Routing rules resolve against
  specialist names (roles used only as model-tier aliases).
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
| Agent lifecycle (wait/terminate/attach) | ⚠️ | send returns response; no completion tracking; `attach` respawns; `_active_agents` never cleaned — fixed in M1.3/4 |
| Chat message endpoint | ⚠️ | persist-only, no agent reply (orchestrator loop missing — M1.7) |
| Agent definitions `sweave/agents/*/config.yaml` | ✅ | loader wired (R0): prompts/tools/harness from YAML, FALLBACK_PROMPTS for gaps; model_template stored for R1 |
| Claude Code / Codex harnesses | 📐 | detect-only (which/--version), no spawn |
| `/ws` realtime | ✅ | `websockets` dep; WSEventBus + unified vocabulary landed in M1.prep; legacy event names preserved |
| OpenCode spawn path | ⚠️ | exe resolution + log-file port discovery + v2 API (`/session`, `parts` body, per-message model, chunked-stream read) — verified against a live serve; resume-across-restart + completion signal still open (M1.3 branch point) |
| `sweave doctor`, `models`, `rules`, `route` | ✅ | `models --reset` ⚠️ stub |
| Web UI v1 | ✅ | 40/40; welcome-mode gating fixed; see test_sidebar_nav.js |
| **Server split into routers/** | ✅ | M1.prep — no import-time singletons, FastAPI lifespan owns AppState |
| **Atomic JSON + per-project locks** | ✅ | M1.prep — `runtime/locking.py`; ProjectManager routes all writes through |
| **JobRunner + Delegation store** | ✅ | M1.prep — `runtime/job_runner.py`; `POST /api/v2/tasks` returns `{delegation_id, status}` |
| **Per-delegation trace log (JSONL)** | ✅ | M1.prep — `~/.sweave/traces/{id}.jsonl` |
| **pytest suite** | ✅ | M1.prep — 62 tests across 10 files; `pytest` is the new source of truth |
| Git history | ✅ | M1.prep — 8 commits; `docs/M1_PREP_PLAN.md` is the plan of record |

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
- **M1.1 Record split** (~1, planned in detail: `docs/M1_1_PLAN.md`): Delegation v2
  (worktree/branch/pr_url, `parent_task_id` deferral chain, `manifest` self-report,
  schema v1→v2 migration), per-project disk persistence (atomic, write-through),
  `SubAgentRun` ephemeral type (in-memory, capped), API filters, ChildSession bridge
  for UI v1 compat. Gate: pytest + test_full.py + test_projects.py green.
- **M1.2 Specialist store + CRUD** (~1): global `~/.sweave/agents.yaml` + project
  `.sweave/agents.json` (name, role-ref, harness, current_model, durable session_id,
  status); resolution project → global → seed templates; orchestrator singleton
  auto-seeded per project (context per-Session per §2.1); specialists CRUD API +
  `PUT /specialists/{name}/model`; **routing-override logging** (every user
  reassignment is stored as an active-learning gold label). Gate: endpoint tests,
  singleton enforcement.
- **M1.3 Shared serve + durable context** (~2, risk sink): `SpecialistRuntime` per
  project — one lazy `opencode serve`, specialist→session map, resume stored sessions
  (feature-detected per M1.0), `fresh:` flag, worktree re-injected per delegation,
  serve health monitor + auto-restart, Windows orphan sweep. Gate: live test — second
  delegation to same specialist resumes context.
- **M1.4 Lifecycle completion** (~1): completion detection (per M1.0), Delegation status
  transitions wired to runtime, terminate-on-done, `_active_agents` cleanup, real
  `attach`, **stuck detection** (heartbeat / output-staleness / timeout — decide per
  M1.0 findings). Gate: 3 consecutive delegations reach done/failed, no process leaks.
- **M1.5 Model at request time** (~1): harness contract `send(message, model)` in
  base.py; OpenCode per-message `providerID/modelID` **already landed via the M1.0 fix**
  (remaining: base.py contract, switch API wiring, idle/running semantics); M1.2 endpoint
  wired to runtime. Gate: idle model switch demonstrably applied to next delegation.
  Cost-budget enforcement: local tiktoken estimates by default;
  **Cloudflare AI Gateway** as opt-in native enforcement for cloud providers (§8 map).
- **M1.6 DelegationManager + deferral** (~1.5): structured `defer{target, task}`
  protocol (prompt convention + output parser), orchestrator-mediated spawn, depth cap
  (default 2), loop detection on the deferral chain, per-chain budget; deferrals recorded
  as Delegations with parent_task_id. Gate: mocked-harness unit tests (defer/loop/depth/
  budget).
- **M1.7 Orchestrator chat loop** (~1.5): messages endpoint routes through the
  orchestrator specialist (per-session context), delegation via M1.6, replies persisted;
  polling status (ws broadcast bonus). Gate: E2E — chat → orchestrator reply → delegation
  in Children tab.
- **M1.8 Streaming** (~1): orchestrator chat + specialist output streamed over `/ws`
  (SSE fallback); delegation progress events from M1.prep's event vocabulary. Gate:
  chat replies render incrementally.
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
- `/investigate`: read-only SubAgentRuns (no worktree, no PR), synthesized findings.
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
