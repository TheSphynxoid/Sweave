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
4. **Human merges.** `git merge` / `gh pr merge` are human-only actions.
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

## 4. Component status (audit 2026-08-29)

| Component | Status | Notes |
|---|---|---|
| WorktreeManager (create/remove/list/PR) | ✅ | server, CLI, delegate all call it |
| RuleRouter matching + `{{templates}}` | ✅ | first-match regex/keyword |
| RuleRouter `_llm_fallback` | ⚠️ | keyword heuristic, no LLM call |
| Task delegation → opencode serve | ✅ | real subprocess + HTTP session |
| Agent lifecycle (wait/terminate/attach) | ⚠️ | send returns response; no completion tracking; `attach` respawns; `_active_agents` never cleaned |
| Chat message endpoint | ⚠️ | persist-only, no agent reply (orchestrator loop missing) |
| Agent definitions `sweave/agents/*/config.yaml` | ✅ | loader wired (R0): prompts/tools/harness from YAML, FALLBACK_PROMPTS for gaps; model_template stored for R1 |
| Claude Code / Codex harnesses | 📐 | detect-only (which/--version), no spawn |
| `/ws` realtime | ✅ | `websockets` dep added; handshake verified 2026-08-29 |
| OpenCode spawn path | ⚠️ | repaired (exe resolution + log-file port discovery, no pipes); live serve verify pending |
| `sweave doctor`, `models`, `rules`, `route` | ✅ | `models --reset` ⚠️ stub |
| Web UI v1 | ✅ | 40/40; welcome-mode gating fixed; see test_sidebar_nav.js |
| Git history | ❌ | **zero commits** — all knowledge unversioned |

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
- **Record split**: rename v1 `ChildSession` into `Delegation` (persistent: worktree,
  PR, status, cost — the deferral/fanout tree) and `SubAgentRun` (ephemeral sub-agent).
  API/DB/UI updated in the same change.
- **Specialist store**: per §2.2 — global `~/.sweave/agents.yaml` + per-project
  `{project}/.sweave/agents.json` (name, role-ref, harness, current_model, durable
  `session_id`, status idle/running); CRUD API/UI for dynamic creation in both scopes;
  orchestrator singleton enforced per project (context per-Session per §2.1); loader
  seeds resolution order project → global → seed templates (`agents/*/config.yaml`).
- **Durable context**: delegation resumes the specialist's stored session on a shared
  `opencode serve` per project; `fresh: true` per task = clean slate; worktree path
  re-injected per delegation (context is conversational — §2.1). Retire
  one-process-per-run in favor of shared serve + per-specialist sessions.
- **DelegationManager**: deferral protocol (`defer{target, task}` results; orchestrator
  performs spawns), depth cap, loop detection on the deferral chain, per-chain budget;
  every deferral recorded as a Delegation (tree visible in Children tab).
- **Model at request time**: extend `AgentProcess.send(message)` with model in the
  harness contract (base.py + all adapters); specialist.current_model switchable while
  idle (queued if running); settings/API endpoint to switch; precedence per §2.1.
- AgentProcess lifecycle: completion detection (opencode session status / idle timeout),
  terminate on done, cleanup `_active_agents`, real `attach`.
- ChildSession records worktree/branch/PR URL + status transitions (queued→running→
  review→done/failed) — mirrors Polly's `.polly/registry.json` as per-project registry.
- Orchestrator chat loop: messages endpoint routes through the supervisor model instead
  of persist-only.

### R2 — Orchestrator skills (Polly's core loop)
- `/fanout`: parallel-safe subtasks → routed across the specialist pool (one Delegation,
  worktree and PR each; overflow queues — §2.1).
- `/cross-review`: implementer's diff → *different-role* reviewer Delegation; blocking
  issues loop back as fixes. (Same-vendor rule becomes: reviewer model ≠ implementer
  model, later different harness.)
- `/investigate`: read-only SubAgentRuns (no worktree, no PR), synthesized findings.
- Skills = skills/{name}/SKILL.md conventions + delegation presets; human merges, always.

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

## 7. Risks / open questions
- OpenCode serve port discovery (probe 4096-4199) is fragile — revisit with `--port 0`
  stdout parsing only.
- No completion tracking yet: "task done" currently = HTTP response received (R1 risk).
- JSON-file storage for projects/sessions is fine single-user; SQLite migration deferred.
- models.dev catalog cache staleness → refresh endpoint in R4.
- Windows subprocess lifecycle (orphaned opencode.exe on crash) — add PID sweep in R1.
