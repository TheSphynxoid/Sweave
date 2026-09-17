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

### The two funnels (user articulation, 2026-09-04)

Sweave is two funnels between the human and the machinery:
1. **Input funnel** (M1.7): one chat thread. The human states intent; the
   orchestrator decomposes and defers. No opencode instances, no session
   switching visible to the user.
2. **Output funnel** (M1.6 gating + M1.7 synthesis + M1.9 surfacing + M1.11 blocking Q&A):
   one promotion queue carrying results, reviews, questions, and escalations to
   the human. The orchestrator asks blocking questions via the `ask_human` MCP
   tool (no timeout; the turn holds until answered or system-confirmed skip);
   specialists escalate to the orchestrator via the `escalate` MCP tool
   (non-blocking notice in the global audit log). The native opencode
   `question` tool is denied on both managed agents.

The funnels multiplex *decisions*, not *work*: with coordination unified, N
specialists run in parallel at zero extra human attention (the view zooms across
the delegation tree instead of the human alt-tabbing across instances). The human
is not stripped from the loop — they are a peer at every decision point (merges,
promotions, escalations); what disappears is the courier work.

Distinct from Polly (our additions): project-scoped everything (agents, memory, sessions
per folder on disk), hierarchical memory banks, model routing by role + rules, Windows-first.

## 2. Core concepts

``
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
Template  PERSISTENT sub-agent definition (prompt + tool policy + model default +
            worktree policy): the opencode-agent-definition shape. Seeds
            (`sweave/agents/*/config.yaml`) are already template-shaped; each
            run is ephemeral (fresh session, retired tree). Reviewer is the
            first carrier — plan `docs/SUBAGENT_TEMPLATES_PLAN.md` (specified
            2026-09-16, tier ruling proposed, not yet locked).
Harness   executor adapter implementing AgentProcess (spawn/send/wait/terminate)
Worktree  git isolation unit, branch sweave/{task_id}/{agent}, PR via gh or REST
Integration branch  per-task branch where clean parallel work auto-merges (after the
            Stage-0 overlap check + cross-review pass); the base branch stays human-only
Manifest  JSON self-report attached to a Delegation (files touched, intent,
            confidence, breaking_change flag) — cheap intent proxy for mediation
Resolution queue  async queue consumed by the Resolution Skill (conflict mediator:
            resolve / re-queue / escalate-human); keeps the orchestrator a router,
            not a chokepoint
Memory    banks: global / project-{name} / session-{id} (local-first backend default per R4.4 re-cut; hindsight opt-in)
Router    TEMPORARY hard-edge fallback: pattern → recommended (specialist, model).
          Primary routing authority is the orchestrator LLM via `defer` tool calls
          (M1.6/M1.7); the rule-router guarantees a result when the orchestrator is
          unavailable or unsure. Roles are MODEL TIERS (models.yaml:
          orchestrator/backend/frontend/reviewer as default-model buckets), not a closed
          agent set; the rule-router resolves against specialist names.
``

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
- **LLM-called fork (user-locked 2026-09-09)**: the orchestrator may fork a
  specialist at runtime (`fork_specialist(base, name?, focus, reason)` MCP tool,
  sibling of `defer`; project scope only) instead of asking the user to clone
  one — e.g. `backend` → `backend-orders` + `backend-payments` on a microservices
  project. Reuse-first (`list_specialists` → reuse if fit → fork only with a
  reason; `forked_from` + reason recorded on the trace). Each fork is a separate
  persistent identity (own `session_id`, own lock) — no threads-under-one-name.
  Gated by `fork_policy: "auto" | "confirm" | "disabled"` (resolution session →
  project → global → `"auto"`): `auto` creates immediately; `confirm` files an
  escalation via the M1.9 store instead of creating; `disabled` rejects the fork
  (`rejected: forking disabled by policy`) and the orchestrator reuses. Creation
   only — existing specialists, `defer`, and manual UI creation are unaffected.
- **Prompt template variables (2026-09-09)**: a specialist's
  `system_prompt` may reference live per-delegation values as
  `{{var}}` (`task`, `worktree_path`, `project_name`,
  `delegation_id`, `today`, `branch`, `git_status`,
  `recent_commits`, …; full table in
  `sweave/runtime/prompt_template.py`). Static prompts keep the
  legacy one-off send on session create; templated prompts render
  fresh and send per delegation (per-turn values would otherwise
  bake the first turn into a reused session). Unknown names stay
  verbatim; `${VAR}` (other apps' spelling) is not expanded.
  Seed views are never persisted: the session saver skips
  `scope == "seed"` (a saved seed materialises a shadowing
  global/project copy that hides the seed).
- **Child runs = traditional sub-agents**: ephemeral, used for exploration/investigation
  (Polly's `/investigate` pattern); no worktree or PR by default. Delegation of
  *implementation* work goes to specialists in worktrees; delegation of *read* work
  goes to sub-agent runs.

### 2.3 Memory openness (user-locked 2026-08-29)

The memory layer decomposes into three independently configurable parts; users pick
per part in config.yaml + Settings:
1. **Embedder** — where vectors come from: Workers AI (`@cf/baai/*`), Ollama (local,
   free, private), OpenAI, or a custom endpoint.
2. **Vector store** — where they live: local file backend (default as of R4.4
   re-cut 2026-09-10: zero-infra naive recall; sqlite-vec later), hindsight
   embedded/docker/cloud as opt-in whole-stack alternatives,
   **Cloudflare Vectorize** (free tier sufficient; REST via httpx; `namespace`
   == bank hierarchy) opt-in. Hindsight docker/cloud remain as whole-stack alternatives.
3. **Logic layer** — extraction/reflect/compaction: hindsight-style, rule-based v1,
   mem0-inspired single-pass ADD-only extraction + Zep-style temporal validity later (R6).
Trade-off recorded: cloud stores are not local-first (privacy + latency) and are
eventually consistent — local embedded stays the default; Vectorize is opt-in.

## 3. Architecture

``
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
│   MemoryTool      recall/retain/reflect ⚠️ (R4.4 re-cut 2026-09-10: tool exists but `POST /api/memory/*` 422s on JSON bodies and no backend is usable by default — contract fix + local-first backend + coverage are the plan; chat degrades to empty memory sections meanwhile)     │
│   ProjectManager  projects/sessions/msgs✅ (JSON store)     │
├─────────────────────────────────────────────────────────────┤
│ Harness layer (sweave/harness)                              │
│   AgentSpec → Harness.spawn → AgentProcess(send/wait/term)  │
│   OpenCode: `opencode serve --port 0` + HTTP  ✅            │
│   claude/codex: detect-only 📐 (no spawn)                   │
└─────────────────────────────────────────────────────────────┘
``

Data flow of a delegated task (today, real):
`POST /api/tasks` → RuleRouter.route → DelegateTaskTool.execute →
WorktreeManager.create_worktree → AgentSpec(prompt, model, worktree) →
OpenCodeHarness.spawn (`opencode serve`, cwd=worktree) → HTTP message → result string.

## 4. Component status (audit 2026-08-29, post-M1.prep)

| Component | Status | Notes |
|---|---|---|
| WorktreeManager (create/remove/list/PR) | ✅ | server, CLI, delegate all call it |
| **Task worktree isolation (runtime path)** | ✅ | Restored: `JobRunner._run` creates `sweave/{task}/{agent}` + tree per implementation delegation (chat stays in-tree); record carries worktree_path/branch; the runtime runs IN the tree with project scope (permission map + own-tree allow root); the tree retires at settle (`done`/`failed`, incl. cancel + human promote) with the branch kept, `review` keeps its tree. Creation failure fails loud (non-git projects must init). Base: project override (absolute as-is, relative anchored at project) else `{project}/.worktrees`; git dir is always the project (CWD-independent). Engine turns share the sidecar (per-turn cwd, no new processes); opencode spawns per-task serves reaped by idle TTL |
| RuleRouter matching + `{{templates}}` | ✅ | first-match regex/keyword |
| RuleRouter `_llm_fallback` | ⚠️ | keyword heuristic, no LLM call |
| Task delegation → opencode serve | ✅ | real subprocess + HTTP session |
| Agent lifecycle (wait/terminate/attach) | ✅ | M1.3 + M1.4+M1.5 — wait returns the final Delegation; terminate kills the per-specialist serve on idle TTL; attach delegates to SpecialistRuntime; `_active_agents` removed (M1.4+M1.5 step 0 — the registry owns the runners; dead dict audited + deleted) |
| Chat message endpoint | ✅ | M1.7 — `POST /api/sessions/{id}/messages` (user role) drives the orchestrator chat loop: persist user message, run the orchestrator specialist via SpecialistRuntime with the Session-bound session-id callbacks (M1.7 step 1), persist the assistant reply. Per-session asyncio.Lock for serial conversation semantics. Non-user roles (system/tool/assistant) keep the persist-only contract. Orchestrator-unreachable or timeout: explicit error message persisted, never a silent fallback. |
| Agent definitions `sweave/agents/*/config.yaml` | ✅ | loader wired (R0): prompts/tools/harness from YAML, FALLBACK_PROMPTS for gaps; model_template stored for R1 |
| **Multi-message chat turns (rounds)** | ✅ | 2026-09-11 transparency fix: one persisted assistant message per orchestrator round (round 0 persists before the child wait, synthesis lands as round 1; `metadata.turn_round` + `turn_final`; `chat.delta`/`chat.thinking` carry `round`; bubbles join on (delegation, round); turn snapshot carries `round`). UI renders intermediates collapsed (RoundBlock) with the children/question lanes on the final message only. |
| Claude Code / Codex harnesses | 📐 | detect-only (which/--version), no spawn |
| `/ws` realtime | ✅ | `webspaces` dep; WSEventBus + unified vocabulary landed in M1.prep; legacy event names preserved |
| OpenCode spawn path | ✅ | M1.0 + M1.3 + M1.4+M1.5 — exe resolution + log-file port discovery + v2 API (`/session`, `parts` body, per-message model, chunked-stream read); SpecialistRuntime wraps one opencode serve per (specialist, worktree) with idle TTL; model path uses structured ModelRef (K-revised) so multi-provider configs (ollama, gmi/gmicloud, zai, opencode default) all route correctly; **M1.4+M1.5 step 1** promotes `ModelRef` + `model_ref_to_wire` into `harness/base.py` (the contract type) and `Message` gains `model: ModelRef \| None` (per-message beats spawn-time) |
| `sweave doctor`, `models`, `rules`, `route` | ✅ | `models --reset` ⚠️ stub |
| Web UI (sweave-web, R4 wave 1) | ✅ | R4 step 4 (2026-09-05) — Vite + React 18 + TS + Tailwind + Zustand + React Query. Wave 1: design system (5 v1 presets + custom-color override, localStorage-persisted — expanded 2026-09-09 to 20 presets across light/dark modes + 37 themeable tokens in 5 groups with per-group reset; see the theme-system note under R4.1), chat with streaming (chat.delta + message.added events; M1.8 no-rerender invariant carried through to React), session picker + composer (closing the M1.9 funnel leak), children live tree (WS-pulsed, depth-indented, escalation lane at the top, promote + answer inline), delegation detail view (composed prompt + tool timeline + tokens + status timeline). Backend serves `sweave-web/dist` (the SPA catch-all + `/assets` + `/favicon.svg`); `SWEAVE_UI_VANILLA=1` forces the v1 fallback. Vitest unit suite (40 tests) + Playwright e2e suite (`sweave-web/e2e/`). v1 vanilla UI retired (git history preserves). |
| Web UI foundation nav (R4.1) | ✅ | R4.1 (2026-09-06) — the project→session tree navigation backbone: `ProjectSwitcher` (dropdown of all projects) + `SessionTree` (always-visible per-project session list with active highlight + inline create-session form) wired into the Sidebar; AppProvider subscribes to the 5 new WS events (`project.created` / `project.deleted` / `session.created` / `session.deleted` / `active_session.changed`) and invalidates the smallest scope of React Query keys (mapping in `src/context/wsInvalidations.ts`, 9 vitest pin the contract). Custom-color editor: 8-token picker (background/foreground/primary/primary-fg/border/muted/muted-fg/accent) + 'Reset to preset' button, mounted in the ThemeSwitcher dropdown (expanded 2026-09-09: all 37 tokens in 5 groups — Base/Brand/Surfaces/Status/Chat-&-code — with search filter, per-group reset, and override badges; presets carry a light/dark mode + description and the switcher/Settings/command-palette pickers group by mode). Scaffold-first: designed stubs for `/delegations/:id` (R4.3), `/memory`, `/agents`, `/settings` (R4.4) with the 'Pending R4.X' badge — honest scaffolds, not fake UI. **Stack upgrade**: React 19.2.8 + Tailwind 4.3.3 (CSS-first `@theme`; tokens carry full `rgb()` values so v4 utilities resolve without arbitrary-value wrappers). 60 vitest, 458 pytest (was 452; +6 from `test_r4_1_ws_events.py`), 13/13 `run.py --check`, `npm run build` green. R4.2/R4.3 adopt assistant-ui runtime + agent-elements-derived cards (per §8). |
| **Server split into routers/** | ✅ | M1.prep — no import-time singletons, FastAPI lifespan owns AppState |
| **Atomic JSON + per-project locks** | ✅ | M1.prep — `runtime/locking.py`; ProjectManager routes all writes through |
| **JobRunner + Delegation store** | ✅ | M1.prep — `runtime/job_runner.py`; `POST /api/v2/tasks` returns `{delegation_id, status}` |
| **SpecialistRuntime (per-specialist ServeRunner + session reuse)** | ✅ | M1.3 — `runtime/specialist_runtime.py` orchestrates one delegation: resolves a ServeRunner (lazy start per (specialist, worktree)), ensures a session (create, recreate on 404, or reuse), sends a single message with structured ModelRef in `body["model"]`. M1.3 step 4 wires a per-turn timeout (default 15 min) and routes success to `review` (M1.4 promotes to `done`). Serve lifecycle (2026-09-10 leak fix): `ServeRunnerRegistry` tracks serve PIDs in `~/.sweave/serves.json`; lifespan reclaims previous-run orphans at boot (dead-owner + port-probe verified), tears everything down via `shutdown_all` (tree-kill on Windows so MCP children die too), and sweeps idle runners every 5 min (the 30-min TTL); `stop_server.py` tree-kills (`/T`). `find_orphan_serves` remains as an un-wired heuristic helper only (its `.worktrees` match can never see project-root serves; psutil was never installed) |
| **Per-delegation trace log (JSONL)** | ✅ | M1.prep — `~/.sweave/traces/{id}.jsonl` |
| **Parts-model trace capture** | ✅ | M1.9 — `sweave/harness/opencode.py` stream reader captures tool parts (pending → running → completed | error keyed by callID), `step-start`/`step-finish` (→ `step.boundary` events with reason / cost / tokens{input, output, reasoning, cache.{read, write}}), per-turn `tokens_used` audit anchor. Terminal detection: turn complete iff `info.time.completed` AND `info.finish` are set (replaces the per-chunk "parts + role==assistant" heuristic). Reasoning parts default OFF (`trace_reasoning=True` flag enables them). Dead `type:"error"` part branch removed; errors come from `info.error`. |
| **Delegation detail view (web)** | ✅ | M1.9 — `sweave/web/detail_view.py` projects the trace JSONL into composed-prompt / tool-timeline / tokens / status-timeline sections (the same data the UI detail view patches in place + the `sweave log` CLI prints). `GET /api/delegations/{id}/detail` endpoint. |
| **Plan board (web)** | ✅ | TRACKING_PLAN Phase A (2026-09-11) — `/plan` route + Sidebar funnel entry: Kanban by status + table + bugs lane (failed-first, then needs_attention, deduped) over existing `GET /api/delegations` (same query key as Children, one cache); pure `pages/plan/board.ts` builder (BOARD_CAP 200, showing-N-of-M note); shared DetailView modal on click; trace-`todo` projection explicitly rejected (0 parts across 6,396 traces). Read-only; tickets = Phase B. |
| **Visibility CLI (sweave log/watch/tail)** | ✅ | M1.9 — `sweave log <id>` (pretty-render trace), `sweave tail <id>` (follow a running turn; `sweave/cli/tail.py:follow_trace` async generator with file-rotation handling), `sweave watch` (live tree; polls `/api/delegations` on the running server). |
| **ask_human MCP tool (blocking questions) + escalate (specialist notice)** | ✅ | M1.9 → M1.11 — `sweave/mcp/__init__.py` `ask_human(question, options?, caller_delegation_id?)` (orchestrator→human BLOCKING question; replaces the native `question` tool, denied on both managed agents) + `escalate(message, caller_delegation_id)` (specialist→orchestrator non-blocking notice; the only sweave tool specialists may call). Records carry `kind` (question\|escalation) + `audience` (human\|orchestrator); `status` adds `skipped` (explicit system-confirmed skip via `POST /api/delegations/{id}/skip {confirmed: true}`, 409 when unconfirmed). No deadline (M1.11 ruling); `deadline_at` null; legacy `timeout` records stay readable, `force_timeout` is a manual/test seam. Store: `sweave/runtime/escalation.py:EscalationStore`. WS: `specialist.escalated` + `specialist.escalation_resolved{status}`. Endpoints: `POST …/escalate`, `/answer`, `/skip`, `GET …/escalation`. ChatLoop holds the turn open (no assistant persisted) until answered\|skipped, then synthesises with the outcome; thread shows an inline Question card, Children audit shows kind badges + previews. |
| **Permission-aware turns (scoped roots + ask handling + silence watchdog)** | ✅ | M1.12 (2026-09-10) — `runtime/mcp_config.py` renders a scoped `external_directory` (catch-all `ask` + built-in roots allow: cwd-subtree via opencode itself, `<project>/.worktrees`, `~/.sweave`; human-declared `Project.permission_roots` via `PUT /api/projects/{name}/permission_roots`; rules are last-match-wins over platform-separator `<root>\*` + `<root>/**` patterns per the binary's evaluate). `runtime/permission_watch.py:PermissionWatcher` keeps a standing `/event` SSE subscription per serve (the ONLY pending surface in 1.18.29 — no list route; no sqlite reads, ruling); `SpecialistRuntime` on a stalled turn converts a pending ask into a blocking human question (`kind=permission`, no timeout, escalated for BOTH roles — user ruling; skip=deny), POSTs the pinned reply, then recovers the final text via `GET /session/{sid}/message` (the original stream does not re-deliver terminal; completion = bus `session.idle`). ChatLoop suspends `turn_timeout` while a human question is pending (shielded re-arm). Inline question card renders the permission detail + the 'always' grant scope. Live gate: `scripts/m1_12_live_gate.py` (silent root pass / once→content / reject→loud). **Amendment 1 (2026-09-10, user-locked): in-band bridge.** The out-of-band stall-branch converter never fired on the 2026-09-10 dogfood incident (two 900s turn deaths on a live `ask`, no escalation created) — the primary ask path is now a bundled opencode plugin (`runtime/permission_bridge_plugin.ts`) copied into the sweave-owned config island `~/.sweave/opencode/plugins/` (injected as `OPENCODE_CONFIG_DIR` into every serve spawn, so standalone opencode never runs it — user ruling; project-dir `.opencode/plugins/` rejected because a plugin is code, auto-executed by every opencode in that dir). The plugin ferries `permission.asked` in-process to `POST /api/permission/hijack` (token-guarded, `web/routers/mcp.py`); `runtime/permission_bridge.py:resolve_hijack_request` scope-evaluates against the project record (single source of scope truth) and auto-allows in scope `once` or creates the same blocking human escalation out of scope, then POSTs the pinned reply itself (the plugin never knows the serve URL — no reply loop through the plugin). `SpecialistRuntime` registers each `(session → serve, worktree, delegation)` triple with the bridge at `_ensure_session`. The out-of-band stall-branch ask-dance is retained as fallback only. Live gate for the bridge path: pending (dogfood will exercise it; extend `m1_12_live_gate.py` with a scene that goes through `/api/permission/hijack`). |
| **Turn-death observability + timer coherence + soft limit** | ✅ | Incident 2026-09-11 (backend re-dispatch rejected by loop detection while the child hung; 17-min silent turn died on httpx ReadTimeout; permission escalation created 30s after death). Three slices: (1) ChainError 409s log code+target+chain-root server-side (were LLM-only); `_send_message` bounds header wait + traces `stream_opened`/`first_byte` so silent deaths classify from the trace. (2) Atomic escalation claim (`EscalationStore.create_or_reuse` under the store lock; second finder reuses — `permission_reused` — instead of overwriting); stall branch waits on bridge-owned holds (`permission_hold_wait`) instead of failing, plus late-answer single-fetch recovery (`permission_recovered_late`). (3) Total budget is soft: first unwitnessed cap expiry files a blocking keep/stop question (`kind=question`, `metadata.soft_limit`, existing inline card) — keep re-arms once, stop fails now as `turn_stopped_by_user`; non-soft holds keep the old path; no store keeps fail-fast. |
| **Per-project worktree_base** | ✅ | M1.9 — `Project.worktree_base` field overrides the global `config.git.worktree_base` for that project. `sweave/runtime/worktree_base.py:resolve_worktree_base` is the seam. Scratch-project convention: the dev repo (cwd) is never its own live-gate target. Plumbed through `POST /api/projects` (worktree_base in the request body). |
| **WorktreeManager.align()** | ✅ | M1.9 — primitive: rebase/merge the worktree branch onto the integration branch's current state. Returns `{"rebased", "skipped_dirty", "no_integration_branch", "noop"}`. **Dirty-skip rule** (commit-authority map, 2026-09-04): never stash-dance a working agent; a dirty worktree is skipped, not autostashed. R2's full protocol (drift budget, conflict resolution, shared-context freshness) lands later. |
| **Specialist permission profile** | ✅ | M1.9 (corrected 2026-09-09; M1.11 roles) — `sweave/runtime/agent_permission.py:render_agent_permission_profile(is_orchestrator=...)`. Orchestrator = `task: deny` + `question: deny` (native Q&A replaced by Sweave tools) + git bash deny (`commit*` / `merge*` / `push*` / `rebase*` / `reset --hard*` / `gh pr merge*` as `{pattern: deny}` mappings). Specialist = `task: deny` + `question: deny` + explicit `sweave_defer` / `sweave_list_specialists` / `sweave_ask_human: deny` (orchestration only; `sweave_escalate` stays allowed so blocked specialists reach the orchestrator); git stays allowed so specialists commit in their branches. Correction: the M1.9 profile was nested inside the `mcp.sweave` server entry (stripped by the schema) with a list value (invalid shape) — it never took effect anywhere. Profiles now render as `agent.<name>.permission` on the managed native agents (next row). |
| **Specialist store + /api/specialists CRUD** | ✅ | M1.2 — `runtime/specialist_store.py` (global `~/.sweave/agents.yaml` + per-project `{project}/.sweave/agents.json`); resolution project→global→seed; `is_orchestrator` flag for the per-project singleton; model precedence chain at submit; `PUT /api/specialists/{name}/model` emits `model.changed {name, model, scope}`; `specialist.created/updated/deleted` events; override log at `{project}/.sweave/override_log.jsonl` (global fallback `~/.sweave/override_log.jsonl`) |
| **/api/agents bridge + render fix** | ✅ | M1.2 — returns `{builtin, global, dynamic}` arrays (the M1.prep dicts were the root cause of the empty Agents tab); description-overwrite bug fixed; routes writes through the specialist store; orchestrator name 409 on create/delete |
| **Delegation v2 schema + per-project persistence** | ✅ | M1.1 — schema_version=2 (worktree, branch, pr_url, parent_task_id, manifest); per-project `{project}/.sweave/delegations.json` via `PerProjectDelegationStores`; v1→v2 migration in `from_dict` |
| **SubAgentRun (ephemeral, capped)** | ✅ | M1.1 — `runtime/subagent_store.py`; per-process, in-memory, FIFO-capped at 500; serves R2's `/investigate` |
| **UI v1 compat bridge (ChildSession)** | ✅ | M1.1 — `ChildSession.delegation_id` field + JobRunner bridge write on submit; R4 removes the bridge |
| **DelegationManager (depth / loop / budget)** | ✅ | M1.6 — `runtime/delegation_manager.py`; per-process gate; depth cap (default 2), loop detect (per-chain active set), coordination-token budget (default 200K, tiktoken cl100k_base estimate). `ChainError` subclasses (`DepthExceededError` / `LoopDetectedError` / `BudgetExceededError`) all raise 409 with a "rejected: <reason>" surface for the MCP `defer` tool. Top-level delegations bypass chain rules (no parent = no chain); first defer establishes the chain root |
| **Sweave MCP server (stdio, defer + list_specialists)** | ✅ | M1.6 — `sweave/mcp/` package; `python -m sweave.mcp`; official `mcp` SDK (MIT, §8); two tools: `defer(target, task, reason, caller_delegation_id)` posts to `/api/v2/tasks` with `parent_task_id` and surfaces the DelegationManager's "rejected: ..." lines as plain text the orchestrator can act on; `list_specialists()` returns the resolved pool (excludes the orchestrator singleton). Shared token at `~/.sweave/mcp_token` auto-generated; localhost-only auth via `X-Sweave-MCP-Token` |
| **Per-project opencode.json plumbing** | ✅ | M1.6, extended 2026-09-09 — `runtime/mcp_config.py`; `ensure_mcp_config(project_dir)` is idempotent (the `_sweave_managed` marker per block; user-edited blocks are preserved); triggered on `POST /api/projects/{name}/active` so the orchestrator's serve cwd sees the sweave MCP server automatically. Now also renders the managed opencode-native `agent` map (`sweave-orchestrator` with the YAML prompt + orchestrator profile; `sweave-specialist` with the generic charter + specialist profile), which `SpecialistRuntime` pins per message (`body["agent"]`). MCP entry carries `cwd` = Sweave root (the package runs from source, so bare `python -m sweave.mcp` only resolves in the repo dir). |
| **Orchestrator defer tool contract** | ✅ | M1.6 — `sweave/agents/orchestrator/config.yaml` prompt contains the defer tool spec (args, return shapes, "rejected:" / "error:" / "queued:"); the headline rule narrowed 2026-09-14 to "Never edit, write, or run commands yourself" (the orchestrator owns read-only `read`/`grep`/`glob` for repo-factual Q&A — a few reads beat a delegation round-trip). Follow-up rules gained the no-poll clause: `defer` never blocks inside the turn — after `queued:` end the turn, the follow-up turn carries the results; re-deferring while the child runs only burns round-trips against loop detection |
| **Parent gating (tree lifecycle)** | ✅ | M1.6 — `JobRunner._wait_for_children` blocks the parent's `review` transition until every child delegation reaches `done` or `failed`; bounded by `turn_timeout` so a wedged child can't stall the parent. Trace records `children_settled` (count, done, failed) or `children_settle_timeout`. **Synthesis generation (re-prompting the orchestrator with child results) is M1.7 scope** — M1.6 delivers tree lifecycle + gating only; M1.7 step 3 delivers the synthesis loop for the chat path (server-composed prompt, server-built children summary, second orchestrator turn) |
| **Chat loop (orchestrator conversation)** | ✅ | M1.7 — `sweave/chat/loop.py` ChatLoop drives `/api/sessions/{id}/messages` for user messages: per-session asyncio.Lock for serial semantics, builds a chat Delegation (kind=chat, depth=0, no worktree), runs the orchestrator specialist via SpecialistRuntime with Session-bound session-id callbacks (M1.7 step 1), persists the assistant reply. **Auto-`done` on success** (M1.7 step 3; implementation children still stop at `review`). Orchestrator-unreachable or timeout: explicit error message persisted, never a silent fallback. Concurrent calls on different sessions run in parallel. |
| **Synthesis loop (chat + children → second turn)** | ✅ | M1.7 — after the orchestrator's first turn, ChatLoop scans for child delegations (parent_task_id == chat_d.delegation_id). With children: bounded wait, server-composed synthesis prompt (per-child {specialist, task, status, output, error}, oldest-first truncation, ~8K token cap, tiktoken heuristic), second orchestrator turn, final assistant message. Without children: fast path, first turn's reply is the final answer. Children failing: synthesis still runs with the failure noted. |
| **Runtime transcript system (per-turn composed prompt)** | ✅ | M1.7 — `sweave/chat/transcript.py`; the runtime owns the per-turn composed prompt (the LLM is a consumer of what the runtime built). Sections: curated memory (top-k=5, ~2K), multi-source "what's new" (memory entries with ts > last_recall_ts + git diff since last_snapshot, ~1K), synthesis (when children), one-paragraph transcript reference (~100), user message. The composed prompt size is O(memory + synthesis + user), NOT O(transcript_length) — long conversations don't bloat the per-turn prompt. Trace records per-section sizes + dropped counts. |
| **Per-Session orchestrator binding** | ✅ | M1.7 — Session gains `orchestrator_session_id: str | None` (M1.7 step 1); the durable opencode session id lives on the Session record, NOT on the Specialist record. Three Sweave sessions of the same project get three independent orchestrator contexts. Legacy session files (no `orchestrator_session_id`) load with `None`; the next turn's runtime call creates the binding. `SpecialistRuntime.run` + `_ensure_session` accept `session_id_getter` / `session_id_setter` callbacks; default is the M1.3 Specialist-record behaviour. |
| **Delegation v3 schema (chain metadata)** | ✅ | M1.6 — schema_version=3; new fields `depth` (0 for orchestrator, +1 per defer), `chain_root_id` (None for top-level; the root of the deferral chain otherwise), `coordination_tokens` (tiktoken estimate; coordination traffic only — specialist internal work is opaque by design). `from_dict` migrates v2 → v3 and v1 → v3 |
| **Human promotion (review → done) endpoint** | ✅ | M1.4+M1.5 — `POST /api/delegations/{id}/promote`; 409 from non-review; 404 unknown; trace `status_changed` (source=human_promote); WS `delegation.status_changed`; bridged `ChildSession.status` synced to `done`; Children-tab "Mark done" button (review only, offsetParent-verifiable). **R2's cross-review calls this same endpoint programmatically** — the API is the automation seam |
| **pytest suite** | ✅ | M1.prep + M1.0 + M1.1 + M1.2 + M1.3 + M1.4+M1.5 — 295 tests across 27 files; `pytest` is the source of truth |
| **User default in config.yaml (not models.yaml)** | ✅ | Fast-track 2026-09-11 — `set_default_model` writes `models.default` into config.yaml (surgical line edit, comments preserved, atomic); models.yaml is providers-only (`write_registry` dropped the `default` param, `sync_registry` never reads it). Precedence: config > customs overlay > legacy models.yaml key (adopted once into config on load iff selectable and no customs default; legacy key left in place, ignored). `POST /api/models/regenerate` calls `sync_registry` in-process (the old shell-out to `generate_models.py` never wrote the file). |
| **Estimation records (record-only)** | ✅ | M2.0 2026-09-11 — Delegation schema v7 (`estimate: {tokens, seconds} | None`, `_migrate_v6_to_v7`); `POST /api/v2/tasks` + MCP `defer` accept optional estimate (non-negative, unknown keys ignored, all-null normalises to None); estimate-vs-actual folded into the detail projection (`estimate_vs_actual`: estimate echo + trace `tokens_used` summed + created→completed seconds; nulls on missing trace/record) + `sweave log` panel. No enforcement, no calibration (M2.5), no chat-turn estimates. |
| **Wait-set flag + review-request** | ✅ | M2.1 2026-09-12 — schema v8 (`blocking: bool = False`, `review_request: ReviewRequest | None`, `_migrate_v7_to_v8`); `POST /api/v2/tasks` + MCP `defer` accept optional `blocking` (non-bool via defer → `rejected:` line); success→`review` transition attaches the request (reviewer hint, diff pointer, manifest summary/confidence) + `review_requested` trace event, failure attaches nothing, `promote` keeps it as history; both waits share one rule (`JOIN_SETTLED_STATUSES` + `in_join_set`/`is_join_settled`): only `blocking` children join, `review` counts as settled, empty join set returns immediately, `wait_set_scoped` names the skipped set; synthesis surfaces pending requests (resolve explicitly via `defer(target=reviewer)` — no verdict payload, M2.2); `review_request` folded into the detail projection. No UI changes, no `blocking` on chat turns. **Amended 2026-09-14 (user ruling): omitted `blocking` on a chat-turn defer joins by default** (`resolve_blocking`: explicit wins, else chat-parent → True, else False) — the orchestrator defers because it needs the answer; record schema unchanged (still `bool = False`), the default lives at submit time |
| **Review bundle + record header + attention trigger** | ✅ | Review deepening Phase 1 2026-09-12 — schema v10 (`review_bundle: {path, bytes, truncated, scope} | None`, `_migrate_v9_to_v10`); entering `review` captures the diff artifact synchronously to `{project}/.sweave/reviews/{id}.diff` (worktree-vs-base + untracked files as marked sections; manifest-files or honest unscoped in-tree fallback; degraded captures store a pointer without a file, `missing:<reason>`); all bodies pass the Phase-1 redaction boundary (known secret shapes → `[REDACTED:<kind>]`, full vault still R4.4); 256KB cap with truncation recorded. Detail payload gains the `record` header (status/agent/task+140-char snippet/output summary 2000 chars/error/stamps/blocking/attention) + bundle pointer echo; `sweave log` prints a pointer line only. `needs_attention` means "answer OR promote": set on review entry, cleared on promote (pending question keeps it); the production store flagger shares the single `_review_owes_promotion` rule so answer/skip/timeout never clear while a review is owed. No verdict payload (M2.2), no auto-assignment (Phase 2), no UI changes. |
| **Engine protocol v1 (frozen)** | ✅ | Custom-engine step 0, 2026-09-13 — `sweave/engine/protocol.py` (zero-I/O constants + validators, 27 hermetic contract tests): `POST /run` → SSE, `GET /health`, `POST /abort` (acknowledged — 2026-09-17 ruling: no unconfirmed state), `POST /revert`; trace vocabulary adopted verbatim from the M1.9 anchor (parity-pinned against the real harness helper) |
| **Engine sidecar (chat + tools)** | ✅ | Custom-engine steps 1–2, 2026-09-13 — zero-dependency Node `sweave-engine/src/` (`serve.js` + `loop.js` + `tools.js` + `sweave.js` + `sessions.js` + `providers.js`): true token streaming, 6-tool executor (read/edit/write/bash/glob/grep/todo) with blind permission-map enforcement (allow/ask/deny, last-match-wins, external_directory), agentic loop with per-tool budgets + doom-loop guard + structural role gate, durable sessions, full-catalog auth (named `auth_missing`, never cryptic). Terminal provider failures on BOTH single-shot flavors end the turn loudly in place (failed record + error SSE + res.end — the responses branch used to re-throw past committed SSE headers, hanging the client until the turn timeout; fixed 2026-09-14 after the zen suite wedged). Opt-in only — opencode stays the default until step-4 parity flip |
| **Engine harness adapter** | ✅ | Custom-engine step 1, 2026-09-13 — `sweave/harness/engine.py` (`SweaveEngineHarness`, registered in `harness_registry` alongside opencode): mirrors `OpenCodeProcess.send` (per-message model wins, optional `on_chunk` per token, frozen-vocab trace parity), lazy sidecar spawn with port discovery, loud `ProtocolMismatch` on version drift |
| **build_context() + basics standards** | ✅ | Custom-engine step 3, 2026-09-13 — `sweave/chat/context.py` (engine-agnostic, server-side): AGENTS.md chain (LF standard: global→project→worktree, 32 KiB cap, session-cached content-gated) + SKILL.md index/body (agentskills.io spec, read-not-run v1) + cross-section budget with `context.built` audit; `transcript.py` gains the standing sections, `loop.py` the trace site. Compaction mechanics adopted (opencode, MIT — prompt lifts with the engine compactor + `THIRD_PARTY_NOTICES`); todo shape mirrors opencode `todowrite` |
| **Engine permission endpoint** | ✅ | Custom-engine step 2, 2026-09-13 — `POST /api/engine/permission` (`sweave/web/routers/engine.py`, same MCP-token guard): engine `ask` → blocking human escalation (kind=permission, no timeout) → once\|always\|reject (skip/timeout fail closed). No scope re-evaluation (the rendered map already encodes scope); the opencode bridge plugin + hijack endpoint are not transferred |
| **Turn cancel (Stop button)** | ✅ | 2026-09-14 — `POST /api/sessions/{id}/turn/cancel` (404 when idle): stops the whole subtree — live children first via `JobRunner.cancel_subtree` (tasks cancelled, records failed with `[chat error: cancelled by user]`, `review` children untouched, single-writer via `_user_cancelled`), then the parent turn (`ChatLoop.cancel_turn`); pending escalations on the stopped set resolve as skipped; the partial reply persists as a `cancelled` assistant bubble (no lost turns — the 2026-09-14 server-kill incident). Status stays `failed` (closed `VALID_STATUSES` untouched); UI stop affordance is live. **No-rotation ruling**: a stop kills the work, never the conversation — the binding is always kept (`SpecialistRuntime.abort_live_turn`: sidecar abort; serve abort + serve-restart fallback on opencode; `turn_killed` trace). Kill guarantee is sidecar-enforced (abort signal into tools, bash child kill, abort-aware permission/fetch) |
| **Per-specialist harness selection (fallback REMOVED)** | ✅ | Custom-engine step 4, 2026-09-13 — `resolve_harness_name()` (`harness/base.py`: override > mock > specialist > project > config, then unresolved — NO silent fallback tier, `harness_selected` trace); defaults flipped (Specialist field, seed YAMLs, transients, API/UI create); per-task override (`POST /api/v2/tasks {harness}`, transient); Agents `HarnessBadge`; CLI exports `SWEAVE_API_URL` for sidecar callbacks. **2026-09-14 (user ruling: clarity over obscurity): the automatic engine→opencode retry is REMOVED** — an engine-selected turn that fails before any work fails loud (`[chat error: engine_failed_before_work: ...]`, single wrap), never silently re-runs on a history-less fresh opencode session. **2026-09-14 (user ruling: no harness fallback at all): the `("opencode", "fallback")` selection tier is REMOVED** — `resolve_harness_name()` returns `(None, "unresolved")` when no tier names a registered harness (registry verified again at dispatch, so an explicit override naming garbage fails loud too: `[chat error: harness_unresolved: ...]` with all four tier values). `HarnessRegistry.get_default()` removed (zero callers). Fail loud across harnesses; fail over only within one. Global default settable via `PUT /api/harness/default` (Settings → System → Runtime defaults) |
| **Seed harness override + location-aware lookup** | ✅ | 2026-09-14 — seeds carry a `harness_override` (None = inherit YAML), merged by `_seed_view` alongside the model override; `PUT /api/specialists/{name}` accepts harness-only/model-only on seeds (prompt/desc/role still 409/400 seed-owned); list/resolve serve the merged VIEW (raw overrides no longer leak stale baked defaults); `SpecialistResolver.locate()` finds records by file location (fixes PUT 400 on pre-2026-09-11 mis-scoped project-file copies + global DELETE 404); `GET /api/specialists/orchestrator` exposes the singleton (read-only, no auto-seed); Agents shows a locked orchestrator card + seed Edit (harness-only); `Project.default_harness` flipped to sweave-engine (display-only, nothing reads it) |
| **Two-file config (global + per-project)** | ✅ | User ruling: `config.yaml` holds defaults for every project; `{project}/.sweave/config.yaml` overlays `models` / `routing` / `harness` field-by-field (`ConfigManager.get_for_project`; `server` / `memory` / `git` never overridable; bad overlays fall back loudly). Task scope, not focus scope: both specialist factories take `(agent, project)` and resolve against the delegation's/session's own project (the old closures read the UI-focused active project — a cross-project leak), per-turn timeout/retries/harness-tier/model-default come from the turn's project overlay, and the harness tier gains `project` between `specialist` and `config`. `GET /api/projects/{name}/config/effective` exposes merged config + overlay provenance (Settings names the layering; project editor deferred). Known wart (untouched): the `routing:` block in global `config.yaml` is superseded by `rules.yaml` at load — global routing scalars live in `rules.yaml` (readable via `GET /api/rules`, writable for retries via `PUT /api/rules/retries`; per-project overlay still wins per turn, so a turn stuck at 0 means that project's `.sweave/config.yaml` — check `GET /api/projects/{name}/config/effective`) |
| **Engine session resume guard** | ✅ | 2026-09-14 — `_engine_session_resume()`: only `eng_*` ids attach; foreign (`ses_*`) stored ids spawn fresh (charter re-injected, binding re-minted). Mirror on the opencode side (same day, live incident): `_ensure_session` ignores non-`ses_*` stored ids (no verify GET — a foreign id can answer non-404 and then die in `_send_message`'s guard) and only 200 reuses (404 + any other status recreate). Old sessions never break — history doesn't transfer across harnesses, the opencode conversation stays intact in opencode.db and is reachable by switching back |
| **Engine thinking text (protocol v2)** | ✅ | 2026-09-14 — thinking-inclusion ruling (vercel/ai-pattern baseline, pinned v7.0.99, re-implemented zero-dep): sidecar captures reasoning deltas on all three stream paths (loop chat via `extractReasoningDelta`: `reasoning_details`[] wins, else `reasoning`, else `reasoning_content` — first-non-empty so OpenRouter's doubled shape forwards once; loop + single-shot responses via `response.reasoning_summary_text.delta` + compat variants) and emits `reasoning` SSE (never output, never session history — opencode parity); protocol v1→v2 adds the vocab item; Python forwards via `on_reasoning` (harness → runtime → chat loop → `chat.thinking` live + `metadata.thinking` persisted, the handoff-memo source). Live proof: hermetic stub shapes (deepseek + openrouter) green; free-tier reasoning counts observed live (`reasoning: 15` on a real turn). Follow-ups: responses requests carry `reasoning: {summary: "auto"}` (opencode parity — without it the Go gateway never opens the reasoning channel for muse-spark and inlines thinking into output); persisted `metadata.segments` keeps arrival-ordered text/reasoning for interleave fidelity (UI renders ordered groups, `thinking` stays as the joined back-compat copy) |
| **Chat tool transparency (opencode-style rows)** | ✅ | 2026-09-14 — turns show what they did, not just what they said: harnesses report every tool transition via `on_tool` (opencode v2 parts + engine SSE, normalized by `sweave/chat/tools.py`), the runtime forwards through `SpecialistRuntime`, `ChatLoop` emits `chat.tool` WS events (one per transition, keyed by delegation/round/callID, latest-status-wins) and persists compact rows on `metadata.tools` (reads one-liners `Read path`; edit/write keep capped input for the expandable diff; 100-row cap). UI renders a `TurnTools` activity block above the answer (edit rows reuse the `EditTool` diff card). Delegation-generic (future specialist chats reuse the pipeline); snapshot recovery unions missed transitions; cancel bubbles keep their rows. UI-only by construction: the composer reads role/content/superseded only, so tool rows can never enter the model context (provider-visible tool history stays in the harness session via resume — opencode parity). Side fix: the opencode `_send_message` reader now traces `tool.*` (the runtime path previously dropped tool parts, so chat-turn traces never saw them) |
| **Chat timeline interleave + sticky lanes + modal portal** | ✅ | 2026-09-14 — turns render one ordered timeline (thinking → tool → thinking → text at arrival positions, live and persisted): first-seen callIDs take a position in the arrival log (`segments` gains `{kind: tool, callID}` markers, persisted when reasoning or markers exist — text-only tool-less turns keep the legacy shape); the adapter keeps a live op log per bubble so streaming and finalize render identically (streaming text stays plain-verbatim per the AssistantTextPart rule). Children/question lanes mount on `isActiveTurn`, not message finality — the specialist box survives the round-0 → synthesis gap and intermediate rounds auto-expand while live; `DetailView` portals to `document.body` (escapes the thread stacking trap that painted it under the composer). Marker-without-row drops instead of a hole |
| **Exec-tool output cap + real cache telemetry + usage ledger** | ✅ | 2026-09-14 — token-bloat root causes (the 5.4M-turn inquiry): only `bash` truncated outputs (32K); a limit-less `read` of a ~600KB file dumped 607K chars into session history and re-billed it every remaining iteration. `executeTool` now caps ALL exec-tool outputs/errors at 32K with an honest marker (`capResult`; bash excluded — already truncated at source). Cache telemetry: the loop-turn `tokens_used` anchor hardcoded `cache_read/write: 0` while per-step boundaries carried real numbers — `runLoop` now totals `cached_tokens` and the anchor emits them (single-shot paths already did). Ledger (`sweave/stats/ledger.py`, `docs/USAGE_LEDGER_PLAN.md` surface 2): pure projector over records + trace anchors — totals + per-day/model/project/agent/kind/status/error-class cells, compute-on-read, no new writes, never text; `GET /api/stats/summary?days=` + `/stats` page (totals cards, splits, failure classes). Compaction stays limit-triggered future work (user ruling: never blind) |
| **Role-aware loop budget + stuckness trip + handoff** | ✅ | 2026-09-14 — the flat 50-iteration cap killed healthy implementation turns (two confirmed max_steps deaths on succeeding loops) while stuck turns burned the full budget. `runLoop`: role-aware ceiling (orchestrator 50 — its read-only turns never needed more observed; specialist 150 — 3x observed healthy need), stuckness trip (5 consecutive tool iterations with zero successes — catches the A-B-A-B alternation the identical-call doom guard misses; thinking-only iterations exit via done; sweave-tool successes count as progress; doom rejections feed the streak — ignoring five straight tells trips), and a resumption handoff on every trip (`step.boundary` reason `max_steps`/`no_progress` + turn totals + tool count + first-50 files touched; existing vocab, no protocol change). Trips stay loud (fail-loud ruling) — the handoff makes resume possible, not automatic. **2026-09-15 amendment (failure-cap bootstrap): totals no longer govern health** — specialist ceiling 150→300 (pure cost backstop) + cumulative-failure trip (`failure_volume`: 50 specialist / 15 orchestrator ≈1/3 of role ceiling; doom rejections feed it; handoff gains `failedIterations`). Static guards explicitly marked bootstrap, not loop detection (locked destination: reasoning-loop detector, PLUGGABLES extraction). **2026-09-15 amendment 2 (burst rule + shell grounding**, incident b8544168fa59: 39 successes/12min died on 5 fails in 23s of Unix-on-CMD flailing): streak trips only on tight bursts (5-in-120s; slower feeds volume only); best-offer Git Bash on Windows (explicit paths, never PATH order; `shell` option, verified pipes+codes) with the shell named in the dynamic bash description + one-line charter contract in all 3 seeds; trip messages point at keep/stop. **2026-09-16 amendment 3 (orchestrator ceiling 50→150, volume 15→50):** the same death class reached the orchestrator — healthy planning turn `chat-295a73694c49` (SPECIALIST_VIEW amendment doc work: 50 iterations, 76 calls, only 5 failed) died at 50. Root cause: the "orchestrator never needs >20" assumption predates the 2026-09-15 `.md`+`todo` widening; planning turns now run 30–40 legit iterations, and 5 exact-edit whitespace failures each dragged ~3–4 blind re-reads (≈15–20 burned iterations — the waste half; close-match edit hint built same day at `docs/EDIT_HINT_PLAN.md`). Forensics: 41 reads = 38 unique windows (coverage, not flailing). Takes effect on sidecar recycle (shared node process per server run) — restart to pick up. **2026-09-16 amendment 4 (soft cap — ceilings ask, never kill):** the ceiling carries no proof of no-progress, so a trip files the existing keep/stop question instead of failing (keep = another full window, same tree + resumed session, re-asked per hit; stop = turn_stopped_by_user; no store / foreign pending ask = today failure path, never overwrite a live ask). Runner turns + chat first/synthesis turns (answered ceiling Q&A is turn management — traced, never synthesized). Same ruling closes the last timer-vs-question hole: join waits (chat synthesis + runner parent gate) hold open while a joined child has a pending escalation (wait_join_held), instead of expiring into a partial join |
| **Orchestrator mailbox (escalation auto-seen)** | ✅ | 2026-09-14 — specialist `escalate` notices (`audience: orchestrator`) were acknowledged only by humans while the actual audience saw them second-hand. Mailbox rule: the chat loop resolves consumed notices as `seen` after the synthesis turn that incorporated them (questions/permission never touched; failed synthesis keeps notices pending for the human fallback). Store `mark_seen` mirrors the resolve paths (pending-only, `escalation_resolved` event, review-aware flag clear). UI: notices render FYI ("for the orchestrator — no decision needed", dismiss action with notice-worded confirm) instead of decision cards; stragglers file after synthesis stay dismissible by humans |
| **Per-specialist worktree policy (isolation toggle)** | ✅ | 2026-09-14 (user ruling: per-specialist, never an LLM parameter — the defer contract is untouched) — `Specialist.worktree_policy`: `isolated` (default, today's per-task tree, zero behavior change) / `inherit` (parent delegation's tree; parentless or treeless parents — incl. the treeless chat turn — fall back to project root) / `none` (project root, no tree). Resolved at dispatch in `JobRunner` (specialist resolution moved before tree creation); ownership recorded as `worktree_owned` (Delegation schema v11 — pre-change trees owned by definition) and only the creator retires at settle (shared children + cancel paths converge in `_transition`). Seeds settable via override merge (reviewer seed is the headline case; choosing `isolated` explicitly is behaviorally identical to default); API-validated 400s; Agents edit/create dialogs + card badges |
| **Engine is the spec, opencode the parity baseline** | ✅ | 2026-09-15 (user ruling) — new integration capability lands engine-first and is never cut down to what opencode's bus exposes; opencode stays green (contract tests, live gates) but never gates engine progress. Prior art: 2026-09-14 fallback removal (fail loud across, fail over within). Consequence: specialist-view, reviewer tooling, gated reads, and the progress supervisor all build against engine semantics first; opencode gets ferries (bridge-plugin pattern) where cheap, documented gaps where not |
| **Specialist transcript + live child forwarding (backend)** | ✅ | 2026-09-16, SPECIALIST_VIEW Amendment (Q1–Q6 user-locked, backend slice 2a+2b+2c+4c-backend) — (2a) the native-engine sidecar journal (`~/.sweave/engine/sessions.json`, `SWEAVE_ENGINE_DATA_DIR`) projects into per-turn `transcript` blocks through the additive detail-fold key (`sweave/web/transcript_view.py`; one block per `/run` turn, `user_message_id` protocol v3 names the prompt unit; prompt actually sent + assistant text + tools with lifecycle (assistant `toolCalls` joined with journal `role:tool` results by toolCallId) + reasoning joined from trace `reasoning` chunks via `engine_user_message` boundaries + per-turn tokens; 20K/8K caps with honest markers). Degrade contract: missing journal / unknown session / unknown shapes degrade field-by-field or to null — never raise; opencode turns + pre-change records carry the key contentless (key name LOCKED 'transcript' — glyph-identical with the frontend's 12f2788 binding, landed concurrently). (2b) the opencode send sites trace `wire_prompt` — sizes + bounded 200-char preview (mirrors the chat composed_prompt audit; sizes bound trace growth; full task text already rides `prompt_sent`), templated system renders get their `system_render` phase event while static prompts keep the legacy one-off send untouched. (2c) `_emit_opencode_tool` lifecycle re-verified BY GREP (complete on every observed status incl. the started-synthesis on a first error-only part) and the standing 'unknown parts traced raw' requirement closed: both readers emit `unknown_part {type, raw}` once per type per turn (deep shapes stringified + 300-char cap) — version-bump drift degrades to unknown part rows, never a turn failure. (4c) `JobRunner._run` forwards `on_chunk`/`on_reasoning`/`on_tool` into the runtime for child turns (the freeze's second mechanism: both harnesses honor the callbacks but JobRunner used to pass none); ADDITIVE WS names locked — `specialist.delta` / `specialist.thinking` / `specialist.tool` (new names, keyed by the CHILD delegation_id, no session_id — chat consumers could never sweep child fragments; text/thinking coalesce on child-keyed ChatDeltaCoalescers 200ms/64ch, tools one per transition latest-status-wins with a 100-new-calls cap); the 3-event WS invalidation + 3-5s poll fallback (4c-frontend `livePoll.ts`) stand unchanged — these events make an open detail refetch fresher than the poll alone.
| **M2.2 verdict payload (advisory)** | ✅ | 2026-09-15 — schema v11→v12 (`verdict: {decision, comments, confidence, reviewer, decided_at, gotcha_hits, output_claims_checked} | None`, `_migrate_v11_to_v12`, v1→v12 chain pinned); `POST /api/delegations/{id}/verdict` records on `review`-status only (409 otherwise, 404 unknown; `request_changes` requires non-empty comments, 400); advisory by construction — status/flag/promotion untouched (human-promotes ruling stands; R2 automates via promote later); `verdict_recorded` trace + WS event (promote vocabulary); promote keeps the verdict as history; detail fold carries it (unknown id keeps 200 + nulls). `gotcha_hits` + `output_claims_checked` reserved empty/False until the gotcha system + gated reads land |
| **M2.2 fix rounds (direct/supervised)** | ✅ | 2026-09-15 follow-up (user ruling: user-selectable posture, `direct` vs `supervised` naming) — schema v12→v13 (`fix_of` + `fix_round`, `_migrate_v12_to_v13`); routing gains `review_fix_mode` (direct = request_changes spawns immediately, fire-and-forget default; supervised = verdict records a proposal, human spawns via `POST /api/delegations/{id}/fix-round`) + `review_fix_max_rounds` (default 2, 0 disables, bound on both paths — judgment records, retry refuses); `PUT /api/rules/review-fix` + `GET /api/rules` surface both; verdict gains `fix_assignee` (WHO override; mode stays config-side, never LLM); fix child carries parent link + round + `[fix-round N]` task marker, double-spawn guarded 409, detail fold lists `fix_rounds`. Assignee default = original agent, human-substitutable at verdict or fix time. 13 new tests |
| **Orchestrator `.md` writes + `todo` (frozen-symmetric next)** | ✅ | 2026-09-15 (user ruling: orchestrator owns `.md` docs + its own plan tracking) — engine offers `read/grep/glob/edit/write/todo` (`ORCHESTRATOR_TOOLS` — `git` joins under its own row below; still no `bash`, ever); edit/write gated to `*.md` via last-match-wins map (`ORCHESTRATOR_MD_WRITE_MAP`, must ride every orchestrator turn — unknown keys default allow); charter narrows "never edit/write/run" to "except `.md` docs" + todo doctrine (plan steps vs work state, settle rule, stable titles); opencode side is charter-only (profile allows files by 2026-09-09 ruling — documented asymmetry). Gap-2 closed: sidecar journal persists full session incl. todos (pinned by reload test; every tool result append saves synchronously). Same change repairs the orchestrator seed YAML (col-0 list broke parsing; loader skipped it — live charters fossilized; the 4-role test goes green). Locked amendments for the handoff step: symmetric freeze, title-path model contract with human/API IDs, opt-in slice transport |
| **Orchestrator `git` read-only tool (engine-native)** | ✅ | 2026-09-15, plan `docs/GIT_READ_TOOL_PLAN.md` (user rulings: argv-exec verb allowlist, orchestrator-only, no protocol version bump) — sidecar `runGit` (`tools.js`: `spawn("git", [verb, ...args])`, never a shell string; verbs `log/show/status/diff/branch/ls-files/rev-parse`; unknown verb → `rejected:` before spawn; flag denylist structural: bare `-` denied except `--stat/--oneline/-n/--name-only/--porcelain`, `--upload-pack/--exec/-c/--config` denied explicitly; `log` defaults `-n 20 --oneline`, explicit wins; `matchTarget`→verb, `permissionKey`→`git` default-allow; `capResult` 32K head-cut; missing git / non-repo cwd fail loud) + `TOOL_BASELINE += git` (protocol.py + sidecar `KNOWN_TOOLS`, additive — old sidecars reject loud `bad_request`) + `ORCHESTRATOR_TOOLS += git` (specialists keep `bash`, untouched; opencode needs nothing — reads already pass the `bash` ferry). 5 hermetic sidecar tests + wire-mismatch + orchestrator-tuple pins |
| **Supervisor steps 1–2 (one clock + pulsed-dead ask)** | ✅ | 2026-09-15, plan `docs/SUPERVISOR_PLAN.md` (incident f774d84b: healthy 30-min turn killed, output lost). Step 1: runtime forwards the per-delegation budget into engine metadata on runner + chat paths (inner harness/sidecar clocks enforce the outer value — overlay raises work end-to-end); `_bounded_turn` + chat wait collect done tasks (`turn_corpse_collected`); harness bool-timeout reject. Step 2: pulsed failures (timeout or wire-death) file ONE keep/stop question (`pulsed_dead_rerun`); keep = one fresh attempt same-tree/resumed-session (`turn_soft_keep_rerun`); stop → `turn_stopped_by_user`; silent deaths fail straight; second deaths fail straight (one-question-per-turn now global); legacy path untouched. Attempt loop unifies result interpret; shared tail runs once. 17 new tests |
| **Supervisor step 3 (pulse windows + VERIFYING)** | ✅ | 2026-09-15 — `_bounded_turn` waits in 60s slices against a fuse deadline (overlay value; holds suspend, keep/beacon push). HEALTHY pulses reset silently; quiet accumulates (window capped by fuse); VERIFYING probes the serve (`_serve_alive_for`: opencode peek, engine no-ensure `sidecar_alive`) — certain death trips `turn_no_progress`, uncertain asks once or waits with progress (`waiting_with_progress`); fuse trip/extend last. Keep-consumption releases the soft latch (keep buys a full window). Holds/re-arms outrank the fuse at boundaries. 6 new tests |
| **Supervisor step 4 (opencode activity ferry)** | ✅ | 2026-09-15 — bus probe re-run on 1.18.31 confirms mid-tool silence (drift gate holds), but the plugin API exposes `tool.execute.before`: the island plugin ferries tool starts (name + 200-char target, token-guarded) to `POST /api/activity/tool-started`, attributed via the session registry into `tool.started` pulses the supervisor already counts (zero supervisor change). Live gate green on free tier (both bash starts, valid token + session). 6 new tests |
| **Supervisor step 5 (fuse retune + close-out)** | ✅ | 2026-09-15 — P3 enacted: `turn_timeout_s` default 1800→14400 (4h runaway fuse; pulses govern), runner + chat defaults aligned, pins moved, GOTCHAS timer entries rewritten, plan → done. Supervisor complete; remaining tracks: todo-handoff stack, learned loop guard (R6) |
| **Review-hardening bundle (prompts + truncation + settle)** | ✅ | 2026-09-15, plan `docs/REVIEW_HARDENING_PLAN.md` — (1) policy-aware seed workspace: `prompt_template` gains `worktree_policy` + `workspace` (isolated/shared/by-design-fallback/none/chat sentences; inherit-from-chat names the orchestrator's view as intentional, ruling); all 3 seeds use `{{workspace}}` + absolute `{{worktree_path}}` + scratch discipline (fixes the single-brace/pwd-echo bug). (2) engine truncation parity: `read` defaults to 2000 lines with `Use offset=` teaching, `bash` keeps the tail (failures live at end), exit-error summary takes tail 2000 — removes the cause of the `test_out*.txt` file habit. (3) scratch containment: observed shapes gitignored, root cleared, gotcha. (4) settle commits stray WIP (`commit_wip`, sweave identity) before removal so dirty trees retire with work preserved on the kept branch (the `a5884977` leak class); `prune()` passthrough on clean paths; never force-removes uncommitted work. Reviewer doctrine locked: load-bearing gate (verdict M2.2 first), generic review permission (not a hardcoded exception), "prove, don't fix" |
| **Subagent templates (reviewer first carrier)** | 📐 | Specified 2026-09-16, plan `docs/SUBAGENT_TEMPLATES_PLAN.md` (tier ruling PROPOSED, not locked — template persists, runs ephemeral; MCP `defer` stays the only spawn path; verdict stack untouched). Motivation: synthesis-leaked reviewer incident (blocking-true but unjoined) + no exact queue key + standing-state-without-conversation. Sequenced after tonight's live verdict flow; no supervisor dependency (supervisor DONE) |
| Git history | ✅ | M1.prep + M1.0 + M1.1 + M1.2 + M1.3 + M1.4+M1.5 — 24 commits; `docs/M1_PREP_PLAN.md` ... `docs/M1_4_5_PLAN.md` are the plans of record |

## 5. Locked decisions

1. **Standalone, Polly-style** — no omnigent dependency (remove `omnigent[hindsight]` from
   pyproject). Keep Omnigent agent-YAML *shape* as our agent spec.
2. **Engine default, opencode fallback** — `sweave-engine` is the default harness
   since the step-4 parity flip (2026-09-13: per-specialist selection via
   `resolve_harness_name` — override > mock > specialist > config > opencode —
   with automatic per-delegation opencode fallback before any work,
   `fallback_used` trace). Records that never chose follow the new default;
   stored `opencode` values are respected as explicit. Pre-flip history:
   opencode was the default harness through custom-engine steps 1–2
   (opt-in engine until the flip); claude/codex adapters remain
   documented roadmap (R3), not day-one code.
3. **Docs split** — this file = architecture/design; AGENTS.md = how agents work in this
   repo; PROJECT_STATE.md = runtime state + session history.
4. Vanilla-JS no-build SPA; FastAPI serves static + REST + WS.
5. Windows-first (detached servers via scripts, backend-driven file browser).
6. Chat transparency (user-locked 2026-09-09; extended 2026-09-16 — SPECIALIST_VIEW Amendment Q1–Q6): a deferring turn shows inline
   delegation cards in Chat (status + tool timeline from existing surfaces);
   the specialist drill-down is read-only record-keeping — follow-ups go via
   the orchestrator, never a second input funnel. [2026-09-16: engine-first transcript readout (additive key), tabbed DetailView, cards with tail preview, child live-forwarding + poll fallback; tabs built dock-ready for deferred UX-5.]

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
- **M1.8 Streaming** (~1, **done 2026-09-04** per `docs/M1_8_PLAN.md`): v1 scope
  ruling — **assistant-reply token streaming + status transitions only** (specialist
  tool noise stays in traces for the M1.9 detail view). Harness `send(message, on_chunk)`
  callback → ChatLoop coalesced `chat.delta` WS events (~100ms flush) → single
  streaming bubble in Chat; persisted `message.added` stays authoritative.
  Streaming lifecycle: create-once, patch in place (textContent += text), replace
  on message.added via `replaceWith` (no re-render storm; container.innerHTML
  never reset during streaming). Gate: live multi-delta reply rendering
  incrementally, 40/40 UI untouched, 13/13 run.py --check, 400/400 pytest.
- **M1.9 Dogfood pass** (~2, **done 2026-09-05** per `docs/M1_9_PLAN.md`):
  the last M1 milestone — funnel completion + visibility + hardening. 5 steps:
  (1) **parts-model trace capture** — the harness's stream reader now
  captures tool parts (pending → running → completed | error) keyed by
  callID, step boundaries (`step-finish` → `step.boundary` event with
  reason / cost / tokens{input,output,reasoning,cache.{read,write}}),
  and a per-turn `tokens_used` audit anchor. Terminal detection: turn
  complete iff `info.time.completed` AND `info.finish` are set
  (replaces the pre-M1.9 per-chunk "parts + role==assistant" heuristic
  that could prematurely declare success on a delta). The dead
  `type:"error"` part branch was removed; errors come from `info.error`.
  (2) **hardening** — per-project `worktree_base` on the Project record;
  `WorktreeManager.align()` primitive with the dirty-skip rule (never
  stash-dance a working agent); specialist permission profile
  (`sweave/runtime/agent_permission.py`) — orchestrator = `task: deny`
  + git bash deny (commit / merge / push / rebase / hard-reset /
  gh pr merge); specialist = `task: deny` only (specialists commit
  freely in their disposable branches per the 2026-09-04 commit-
  authority map). `runtime/mcp_config.py` injects the orchestrator's
  profile into the per-project `opencode.json` (closes the native
  opencode subagent bypass). (3) **output funnel completion** —
  `ask_human(question, options?)` MCP tool (sibling of `defer`; same
  auth + wire surface). The asking delegation is flagged
  `needs_attention: bool` (Delegation schema v5; `SCHEMA_VERSION=5`,
  `_migrate_v4_to_v5` helper); `sweave/runtime/escalation.py` is the
  persistence + event surface. Endpoints: `POST /api/delegations/{id}/
  escalate`, `/answer`, `GET /api/delegations/{id}/escalation`. WS
  events: `specialist.escalated` + `specialist.escalation_resolved`.
  Timeout (15 min default; configurable) records "no answer received"
  as the placeholder response so the LLM proceeds with best judgment
  rather than hanging. The MCP server reads `SWEAVE_MCP_TOKEN` env
  first (the opencode.json plumbing seam), falls back to the home
  file. (4) **visibility surfaces** — `sweave/web/detail_view.py`
  projects the trace JSONL into composed-prompt / tool-timeline /
  tokens / status-timeline sections (the same data the UI detail
  view patches in place). `GET /api/delegations/{id}/detail` HTTP
  endpoint + `sweave log <id>` / `sweave tail <id>` / `sweave watch`
  CLI. (5) **self-hosting live gate** —
  `scripts/m1_9_self_hosting_scene.py` drives one chat turn end-to-end
  through the HTTP API (mock opencode subprocess; real running
  server; `SWEAVE_MOCK_OPENCODE=1`). Funnel-leak report (every forced
  exit to API/CLI/file) becomes R4's re-planning input. Per-project
  `worktree_base` plumbed through `POST /api/projects`.
  447/447 pytest (was 400 at M1.9 step 0; +47 from the five step
  files). 13/13 `run.py --check`; 40/40 `test_full.py`.
- M1 exit demo: chat → orchestrator delegates → specialist worktree diff reaches review;
  follow-up chat shows durable specialist context; model switched while idle between
  tasks.

### M2 — Backend capabilities (started 2026-09-11; fast-track + M2.0 + M2.1 done)

Ruling locked 2026-09-11: M2 proceeds NOW on the backend; the R4
remainder runs as a parallel user-driven UI track (UI has been
user-derived since the R4.4 intervention — parallel tracks fit
practice). Coupling discipline: every M2 step ships API contracts +
pytest so UI binds later without rework (the M1.9 `detail_view.py`
precedent). Exception: the R4.4 local memory backend returns when
group-memory/lore work starts (M3 at earliest) — nothing in M2 needs
it. Plan of record: `docs/M2_PLAN.md`; taxonomy + locked mechanics
(wait-set `blocking` flag, review-request record, per-specialist tool
enforcement): `docs/PLUGGABLES_PLAN.md` §3.
- **Fast-track (done 2026-09-11)**: user default out of models.yaml
  (see §4 row) — removes the three-writer clobber footgun before the
  series builds on records.
- **M2.0 estimation records (done 2026-09-11)**: record-only
  `estimate` + estimate-vs-actual projection (see §4 row). Seeds the
  query planner, velocity, and denser training rewards.
- **M2.1 wait-set + review-request (done 2026-09-12)**: `blocking`
  join flag + embedded review-request (see §4 row); both waits share
  one settled rule (the :898 fix). Seeds the M2.2 contract record.
- **Review deepening Phase 1 (done 2026-09-12)**: review bundle
  (transition-time diff artifact + pointer) + detail record header +
  answer-OR-promote attention trigger (see §4 row). Makes `review`
  a gate with material; verdicts + reviewer flow are Phase 2.
- Next (each gets its own execution-ready section before it runs):
  M2.2 contract record → M2.3
  per-specialist tool policy (defaults lock at the M2.3 detailing
  round: proposed default-off new servers, locked reviewer,
  allow/deny-only) → M2.4 golden-set v0 → M2.5 dogfood-minimal into
  R6. Out: planner, group memory, reunion runtime, training env/export,
  audit export.

### R2 — Orchestrator skills (Polly's core loop)
- `/fanout`: parallel-safe subtasks → routed across the specialist pool (one Delegation,
  worktree and PR each; overflow queues — §2.1). Merge handling: **Stage-0 heuristic**
  (path-overlap check, 0 tokens) → clean work auto-merges into the per-task **integration
  branch** (principle 4 exception); overlapping work goes to the **resolution queue**.
- **Worktree alignment protocol** (added 2026-09-04, from the concurrent-sessions
  incident): `WorktreeManager.align()` — rebase/merge the worktree branch onto the
  integration branch's current state + sanity gate. Triggers: before PR creation;
  at deferral hand-off when the parent's output is the child's input; on drift budget
  (base moved > N commits). Dirty worktree => skip and defer to the next clean
  boundary (never stash-dance a working agent). Alignment failure = small fresh
  conflict -> resolution queue (early surfacing). Side effect: aligning refreshes the
  worktree's snapshot of shared-context files (AGENTS.md/DESIGN.md) — kills
  stale-doc reads. Precursor: `align()` in WorktreeManager (~half a step, rides M1.9).
- **Commit-authority map** (ruling 2026-09-04): specialists commit freely in their
  disposable branches (required for alignment + PR; provenance trailer
  `Sweave-Delegation: {delegation_id}` mandatory on every specialist commit; align
  commits carry `Sweave-Align: {base_sha}`); the orchestrator **never commits to the
  user's checkout** (no worktree + git-mutation bash denied — M1.9 hardening); the
  human commits/merges main, always. The common "agents don't commit" rule protects
  the human's history curation — Sweave honors it at the merge boundary, not the
  commit boundary.
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
- Native engine (shipped 2026-09-13, steps 0–3: protocol + sidecar + adapter +
  build_context; step 4 pending): `SweaveEngineHarness` registered alongside
  opencode; per-specialist selection (`Specialist.harness`, field predates the
  engine — no migration) + automatic opencode fallback with `fallback_used`
  trace reason; opencode drops to opt-in only after the step-5 parity gates.
- Cross-vendor review then = reviewer on a different harness than implementer.
- Wire drift (ruling 2026-09-11): third-party wires drift under us while
  ours is versioned by us. Every third-party adapter ships a probe gate
  (version check + capability handshake at runner start — generalizes the
  R4.0 `ses_` assertion and the M1.3 manual probes) and branches on the
  resulting capability map, never on assumed shapes; `sweave doctor`
  aggregates `incomplete_turn`/`stalled`/wire-death rates as the drift
  alarm. Wire knowledge stays quarantined in `harness/<name>.py`.
  Full spec: `docs/CUSTOM_ENGINE_PLAN.md` Step 0.

### R4 — Web UI rebuild: sweave-web (re-planned 2026-09-05, done 2026-09-05)
Rulings: stack = sweave-web's (Vite + React 18 + TS + Tailwind + Zustand + React
Query); existing page code rewritten (pre-M1.x, v1 endpoints); wave 1 = daily-driver
core + theming from day one; **flag-day cutover** (no coexistence); AGENTS ground
rule amended (build allowed, dist served not committed). Full plan: `docs/R4_PLAN.md`.
Wave 1 (done): design system (5 v1 presets + custom-color override,
localStorage-persisted), chat with streaming (chat.delta + message.added
events, the M1.8 no-rerender invariant carried through to React via a
ref + textContent patch), session picker + composer (closing the M1.9
funnel leak), children live tree (WS-pulsed, depth = tree indent, status
pills, promote + answer inline buttons, escalation lane at the top),
delegation detail view (composed prompt + tool timeline + tokens +
status timeline). 4 steps landed as 4 commits on `master`. v1 vanilla
UI + v1 UI tests (test_full.py / test_sidebar_nav.js / test_promote_ui.js)
retired. Playwright e2e suite in `sweave-web/e2e/` (CI gate; local
pytest gate uses `playwright test --list` to pin suite registration).
Wave 2 backlog: Memory tab, Agents workbench (the R4-workbench vision from
the M1.2 era), Settings panes (models/routing/memory/catalog picker -- old
UI_PLAN items) + **Specialist gallery** (LobeChat-Market-inspired:
browse/install specialist presets, import/export via Omnigent-spec YAML). Funnel-leak list (M1.9) is the wave-1 spec; all leaks closed
by step 4 (session picker, promote inline, answer inline). Gate: self-hosting
dogfood on wave 1 (real task through the UI; friction list -> R4.1).

### R4.0 — chat session-id hotfix (done 2026-09-05)

Three commits (one step). The chat turn was posting to
`/session/chat-{delegation_id}/message` (an internal id the
opencode serve doesn't recognise) and 500'ing. Root cause:
`SpecialistRuntime._ensure_session` updated the external binding
(`Session.orchestrator_session_id`) but never propagated the
resolved id into `process._session_id`. Fix: `_build_process`
seeds `session_id=""` (no fabrication); `_ensure_session` writes
`process._session_id` in all three paths (create / 404-recreate /
reuse); `_send_message` asserts the resolved id starts with `ses_`
before posting. Wire-shape mock tightened to match the real serve
(rejects non-`ses_` ids with 500; unknown ids with 404); 5 new
wire-shape regression tests in `tests/test_r4_0_wire_shape.py`
(confirmed to fail 4/5 when the propagation was reverted — a real
regression test). **452/452 pytest** (was 447; +5), 13/13
`run.py --check`. Plan of record: `docs/R4_PLAN.md`. R4.1 is now
unblocked.

### R4.1 — UX foundation: navigation tree, theme system, scaffold-first shell (done 2026-09-06)

Per `docs/R4_1_PLAN.md`. Four steps; one commit per step; same
planning/execution method as M1. Steps 1+1b ship theme completion
+ WS event vocabulary; step 1c is the React 19 + Tailwind v4 stack
upgrade (R4.2/R4.3 adopt assistant-ui + agent-elements-derived cards,
both need React 19 + Tailwind v4 — the upgrade lands here, before
shell work would otherwise need migrating); step 2 ships the
foundation nav (ProjectSwitcher dropdown + SessionTree always-
visible per-project session list with active highlight + inline
create-session form); step 3 ships designed scaffolds for the R4.1
surfaces whose feature work lands in R4.3 (delegation detail) and
R4.4 (memory + agents + settings). **458/458 pytest** (was 452; +6
from the step-1b WS-event tests), 13/13 `run.py --check`, 60
vitest (+9 wsInvalidations + the wave-1 51), `npm run build` green
(post step-1c migration). AppProvider subscribes to the five
WS events; the invalidation map is extracted to
`src/context/wsInvalidations.ts` (9 vitest pin the contract; the
mapping is the smallest scope of React Query keys to refresh).
Stack: React 19.2.8 + Tailwind 4.3.3 (CSS-first `@theme`; tokens
carry full `rgb()` values so v4 utilities resolve without
arbitrary-value wrappers). Playwright e2e spec for the foundation
nav + scaffold presence is checked in (`sweave-web/e2e/
foundation-nav.spec.ts`); CI-time concern per the wave-1 pattern
(chromium 1243 dependency not bundled in this repo). Delegation
tree + detail views remain custom (no library covers them);
R4.2/R4.3 will lift cards from agent-elements (MIT shadcn
registry) per §8.

### R4.2 — Chat surface to the quality bar (in execution; step 2-pre shipped 2026-09-08)

**Status: step 2-pre (visual polish) shipped; awaiting the user's visual
gate on `/chat` before 2b/2c.** Step 0 (adapter spike — REJECT
`@assistant-ui/react-opencode`, ADOPT `useExternalStoreRuntime`),
step 1 (custom adapter over our REST + WS contract) and step 2a
(markdown + GFM + Shiki-highlighted code blocks with copy button) are
shipped. Step 2-pre rebuilt the surface against the INSTALLED
assistant-ui 0.15.18 primitive API: per-message dispatch via
`ThreadPrimitive.Messages`, `Viewport` auto-scroll + `turnAnchor=bottom`
+ `ScrollToBottom`, `MessagePrimitive.Parts` Text slots, real
`ActionBarPrimitive.Copy` (hideWhenRunning + autohide="not-last"),
native composer keyboard (Enter/Shift+Enter), welcome screen with
suggested prompts, history skeleton, streaming cursor, timestamps +
delegation badges via `metadata.custom`. Rulings honored: action bar =
copy + timestamp only (edit/regenerate/fork are R4.3, no fake disabled
buttons); composer stop is live since 2026-09-14 (POSTs the
turn-cancel endpoint; the server stops the whole subtree and keeps
the partial reply). The markdown-only
lab was replaced by the real-Thread lab (fixture runtime + Seed/Empty/
Stream controls) per ruling 4. Also fixed this round: an unlayered
universal CSS reset that silently disabled all Tailwind v4 spacing
utilities app-wide, a 3x message-triplication bug, a double
`useSweaveChatRuntime` instantiation, and "Invalid Date" timestamps.
Gates: 459/459 pytest, 81/81 vitest, `npm run build` green, headless-
Edge screenshot gates (`npm run ui:shot` + `sweave-web/scripts/
ui-chat-probe.mjs`) over the real backend.

Thinking capture (2026-09-09, outside the plan steps): reasoning
parts now flow end-to-end — `SpecialistRuntime._send_message` takes
`on_reasoning`, the chat loop publishes coalesced `chat.thinking` WS
events (same envelope as `chat.delta`) plus persists the full text
as assistant-message `metadata.thinking`, and the thread renders a
live-then-collapsible Thinking block (`metadata.custom.thinking`).
Proven live that the default path (`openrouter/thinkingmachines/
inkling:free` via `opencode serve`) emits a single end-of-turn SSE
object with no reasoning parts — so "wait then full text" on that
path is an upstream granularity limit, not an integration fault,
and the Thinking block stays empty there. It paints whenever the
provider exposes reasoning parts. Mock support:
`SWEAVE_MOCK_OPENCODE_REASONING=1` prepends a reasoning part.

Engine-side gap, found + fixed 2026-09-14: the agentic loop's
responses branch (`sweave-engine/src/loop.js providerStream`) never
passed `onReasoning` into `providerResponsesStream` (the chat branch
did), so thinking models on tool turns -- i.e. virtually every real
turn, since orchestrator turns always take the loop path -- streamed
no Thinking block and persisted no `metadata.thinking`/segments until
the first text token. With high reasoning effort that reads as
nothing, then the full text. One-line pass-through, pinned by
`test_loop_path_streams_reasoning` (fails without it: reasoning
absent while text arrives).

Model variants / thinking levels (2026-09-09): opencode models
advertise reasoning-effort variants per model (`GET
/config/providers`: inkling `none/minimal/low/medium/high/max`,
GPT `none/low/medium/high/xhigh`, ...). Sweave was sending only
`{providerID, modelID}`, so thinking ran at provider default with
no visibility. `ModelRef` now carries an optional `variant`,
written per-message to `body["model"]["variant"]` (the serve
schema accepts it; agent configs accept it too). String form is
`provider/model+variant` (`+` because model ids already contain
`/`, `:`, `@`). Live-verified on `inkling:free`: no variant ~
6.9s hidden thinking, `+none` ~2.0s, `+low` ~2.1s — and explicit
variants make the serve emit `reasoning` parts, so the Thinking
block paints. `models.yaml` lists `inkling:free+none`/`+low` as
pickable entries (badge in the ModelPicker); the default is
unchanged. Static agent-level thinking options in the managed
`opencode.json` were deliberately skipped — per-message variants
cover the need with no new schema.

Registry generation (2026-09-09): `sweave models sync` regenerates
`models.yaml` in layers — models.dev base (neutral upstream: ids +
effort-derived variant rows) + serve overlay (custom providers,
serve-only models, extra variants the serve invents that upstream
never declares; verified 87% exact parity, all mismatches in the
serve-knows-more direction) + hand-maintained `models.custom.yaml`
merged at load (never overwritten). Full expansion (10 providers,
1726 rows, 1062 variant rows); `--all` dumps the 213-provider
universe instead of the curated scope. A `models.meta.json`
sidecar carries per-id variants/reasoning_options/limits/
modalities/cost for future utilities (session budgets, prices).
Deterministic (re-syncs are no-op diffs); refuses to write an
empty registry (a shape mismatch once gutted the file; restored
from git). Review with `git diff`; restart the server afterwards
(the registry is read at startup).

Effort dropdown (2026-09-09): variants are NOT registry rows —
the registry lists base models once and `GET /api/models` serves
a `variants` map (from the sidecar) alongside. Every model surface
(Settings default, specialist create, specialist card) uses the
`ModelWithEffort` control: model picker + effort dropdown that
appears only when the selected model advertises variants; the
stored value stays the combined `model+variant` string (parse/wire
unchanged). `set_default_model` accepts suffixed values when the
base is registered and the variant is advertised (or unknown to
the sidecar). Fixed alongside: `_qualify` always prefixes, so
bare ids that start with their provider name (`nvidia/...`,
`openrouter/auto`) produce wire modelIDs matching the serve's
model keys (the old skip-if-prefixed rule silently broke those
44 rows). ACP verdict (same day):
`opencode acp` speaks v1 JSON-RPC over stdio, but this build
emits no `agent_message_chunk`/`agent_thought_chunk` updates at
all (only `available_commands_update` + terminal `usage_update`,
69s turn, null prompt result) — strictly worse than the serve
API for live UX today. No ACP harness; revisit when opencode
implements session/update streaming.

### R4.4 — Memory + Agents workbench + Settings (re-cut 2026-09-10 from shipped reality)

Intervention note (user-locked): wave-1 UI was judged a failure; all later UI
was manually derived by the user, not executor-built from plans. The 2026-09-05
pre-dogfood strawman is superseded (preserved in `docs/R4_4_PLAN.md` lineage
section); the dogfood gate is void — this re-cut IS the friction list.

Reality audit (live-verified): Memory/Agents/Settings pages exist, but memory
is doubly broken — `POST /api/memory/*` with JSON bodies 422s (scalar query
params in `routers/memory.py` vs JSON client), and `hindsight_client` is not
installed (default `hindsight/embedded_slim` unusable; chat degrades to empty
sections). Zero pytest/vitest coverage of recall/retain/reflect; Memory page
has no reflect tab, health, or WS.

Rulings 2026-09-10: local-first file backend default (hindsight opt-in);
hosted-embeddings opt-in with OpenRouter as policy owner (allowlist fetched
from `/endpoints/zdr`, per-request `zdr:true + data_collection:deny`,
`allow_fallbacks:false`, unknown=locked); three retention badges (ZDR /
abuse-retain-disclosed / trains-or-unknown-locked); secret tag-and-vault
(detect → OS-store → pin-local → at-rest encryption); factory fails closed.
Steps in `docs/R4_4_PLAN.md` (contract fix → local backend → pane
conformance → hosted opt-in → secrets → docs).

### R5 — Packaging
- `pipx install sweave`, versioned releases, first public README pass.
- **Remote access (optional add-on)**: `cloudflared` Tunnel + Access in front of the
  local server — use your Sweave UI from anywhere with zero-trust auth, no port
  forwarding (§8 Cloudflare map). Config-gated, off by default.
- **Cross-platform verification**: CI or manual pass on Windows + Linux/UNIX (both prime targets; macOS out of scope by user ruling 2026-09-16 — supported only if it falls out for free, never targeted or verified; start/stop scripts, paths, process handling are the risk spots).
- **Escalation UX v1**: human resolution flow for the R2 resolution queue — see the
  diff3 + manifests, choose resolve / re-queue / escalate, resolution recorded as a
  gold label (feeds R6 dispatch training).

### R6 — Local orchestrator thesis (📐 future bet — the differentiator)

Gated on M1–R2 stability and real task volume. From the 2026-08-29 architecture
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
| Orphan process sweep | stdlib only (`tasklist`/`taskkill`, `os.kill`, port probe) | PID-file tracking (`~/.sweave/serves.json`) + boot reclaim replaced the `psutil` plan — `psutil` was recorded here at M1.3 but never added to `pyproject.toml` nor installed, and the heuristic it served could never match project-root serves | 2026-09-10 |
| Chain cost budgets | `tiktoken` | token counting for per-chain budget enforcement (approximate for non-OpenAI BPE — fine for budgets) | M1.6 |
| 3-way merge simulation | `merge3` | diff3 merge without touching git — resolution-queue payload + Stage-0 overlap checks | R2 |
| Harness tests w/o live opencode | `respx` | httpx mocking; test spawn/send logic deterministically | M1 tests |
| MCP server SDK | `mcp` (modelcontextprotocol python SDK) | official SDK for the sweave defer/list_specialists stdio server the orchestrator's opencode session calls (M1.6) — MIT, small, no loop ownership | M1.6 |
| UI fonts (Inter + JetBrains Mono) | `@fontsource-variable/inter` + `@fontsource-variable/jetbrains-mono` (npm, MIT packages; OFL font data) | self-hosted variable woff2, `font-display: swap` + unicode-range subsets, system stacks stay as fallback so first paint never blocks; no CDN, offline-friendly | 2026-09-13 |

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

License: **MIT** (verified via GitHub API 2026-09-04) - sublicensable,
so it stays compatible whatever license Sweave itself takes (including
a possible future commercial/closed track, undecided).
Reference clone: C:/Users/user/opencode-reference (shallow, read-only, outside
the repo per the scratch-project convention). Mining priorities: server/sdk
streaming event shapes (feeds M1.9 trace capture), session-ui interaction
patterns (feeds the delegation detail view + R4), tui skim only (Go/BubbleTea -
we chose CLI+Web). If code is ever lifted: preserve the MIT notice
(THIRD_PARTY_NOTICES); patterns are free, components mostly violate our
vanilla-JS no-build rule.

### UI stack adoptions (2026-09-06, R4.2/R4.3 direction)
| Library | License | Role | Verdict |
|---|---|---|---|
| assistant-ui (12k stars) | MIT | chat runtime + primitives (Thread/Composer, streaming, retries, a11y); **generative UI for tool calls + inline approvals** (= ask_human surfacing); official `@assistant-ui/react-opencode` adapter to evaluate | **Adopt** (R4.2) - custom runtime adapter fed by our WS events |
| agent-elements (21st.dev) | MIT (shadcn registry - code lands in our repo) | tool cards (Bash/Edit-diffs/Search/Plan/Subagent/MCP/Thinking), Question card (= ask_human UX), streaming Markdown, composer pieces. Requires React 19 + Tailwind v4 | **Adopt selectively** (R4.2/R4.3) - lift cards, wire to our delegation state |
| vercel/ai | Apache-2.0 (verified via npm registry 2026-09-14) | AI SDK: useChat hooks + data-stream protocol - the substrate both above build on; client-side only (our backend protocol stays ours). **Engine pattern baseline (2026-09-14, user-locked):** pinned at **v7.0.99** — reasoning start/delta/end + text≠reasoning≠tool-call parts + SSE stream semantics are the reference our engine mirrors (re-implemented, never depended on); provider reasoning-delta shapes (`reasoning` → `reasoning_content` → `reasoning_details`) are the extractor spec. Trailing is explicit: the pin trails upstream by design, bumps are deliberate commits, never floating. | Substrate + pattern baseline (no runtime dependency; sidecar stays zero-dep) |
| shiki + @shikijs/* | MIT | server-free syntax highlighting for markdown code blocks (bundled langs; code-split by the Vite build) | **Adopted** (R4.2 step 2a/2-pre) |
| @pierre/diffs + @pierre/theme | Apache-2.0 | diff rendering for Edit-tool cards (R4.2 step 2c) | **Adopted** (installed; first use lands with the tool cards) |
| radix-ui primitives + cmdk + class-variance-authority + tw-animate-css | MIT | shadcn/ui kit (dialog/popover/select/command palette/tooltip/...) the shell is built on | **Adopted** (R4.2 step 2-pre shell) |

Stack consequence: sweave-web upgrades to **React 19 + Tailwind v4** (R4.1 step 1c).

### Opencode platform notes (docs read 2026-09-04)
| Capability | Relevance | Action |
|---|---|---|
| Hidden `compaction`/`title`/`summary` agents | opencode auto-compacts its own session context — the engine-specific view is self-managing; coexists cleanly with our M1.7 curated prompt layers | None — validates engine-class scoping |
| Per-project `.opencode/agents/*.md` (frontmatter: model, temperature, **permission**, **steps**) | Specialist config could render as permission profiles (reviewer = `edit: deny`); `steps` = per-turn agentic cost cap complementing our chain budget; our `tools` list maps to opencode's deprecated field — permissions are the migration path | Future Specialist store enhancement (post-M1.9) |
| `permission.task` globs | Specialists' sessions should not spawn native opencode subagents (all deferral must flow through MCP `defer` for depth/loop/budget enforcement) — `permission.task: deny` on specialist agents closes the bypass | **Hardening item — fold into M1.9 dogfood pass** (cheap, closes an untracked-work loophole) |
| ACP (`opencode acp`, JSON-RPC/stdio) | Standard editor↔agent protocol; the custom-engine side-project could implement ACP so ACP-compatible editors drive Sweave specialists directly | Noted in the side-project scope (M1.7 plan) |

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
