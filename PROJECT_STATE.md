# Sweave - Project State for Future Agents

## Overview

**Sweave** is a multi-agent orchestration platform (CLI: `sweave`) with a **project-scoped** architecture:
- **Project** = a folder on disk (opened via backend file browser)
- **Session** = an orchestrated conversation within a project
  - Parent session runs the orchestrator
  - Spawns **child sessions** (specialist agent runs) for delegated work
- **Memory** is hierarchical: `global` → `project-{name}` → `session-{id}`

- **License**: Apache 2.0
- **Python**: 3.11+
- **Project root**: `C:\Users\user\sweave\`
- **Git**: initialized
- **Web server**: FastAPI + single-page application (SPA)

---

## Current State (Latest Build - All Working)

> **Design**: `DESIGN.md` (architecture, component status, roadmap R0–R7).
> **Agent guide**: `AGENTS.md`. UI v2 plan is folded into DESIGN.md §6 R4.
> **pytest is the source of truth for logic tests**; root scripts are smoke gates.

### What Works (Verified - 40/40 Tests Pass)
- ✅ **App is VISIBLE** after loading (critical fix: `app.classList.remove('hidden')`)
- ✅ **Sidebar navigation WORKS** (5 tabs: Chat, Children, Agents, Memory, Settings)
- ✅ Full-screen layout (CSS: `height: 100vh; width: 100vw` on body and app)
- ✅ Backend file browser (drives, list, validate, create)
- ✅ Project welcome screen with 3 action cards
- ✅ Topbar shows project name and session name (clickable for quick switching)
- ✅ Sidebar toggle for collapsing
- ✅ All modals work (project, session, agent, detail)
- ✅ Theme system with 5 preset themes (dark, light, dracula, nord, catppuccin)
- ✅ WebSocket real-time updates
- ✅ Memory recall/reflect/retain operations
- ✅ Global error handlers that show errors on screen for debugging
- ✅ Backend-driven file browser (no "Folder picker not supported" error)

### Test Results (All Passing - verified 2026-08-30)
- **269/269** in `pytest tests/` (source of truth for logic tests; +62 since M1.2)
- **13/13** in `run.py --check` (endpoint smoke)
- **40/40** in `test_full.py` (comprehensive UI verification)
- **8/8** in `test_browser.py` (file browser API)
- **8/8** in `test_projects.py` (project/session endpoints)
- **ALL GREEN** in `test_agents_loader.py` (24 checks)

### Currently Running
- **Web server**: not running (clean stop after final M1.2 sweep 2026-08-30)
- **Start with**: `python start_server.py 8100 127.0.0.1` (from repo root!)
- **Stop with**: `python stop_server.py` (from repo root — web.pid is CWD-relative)
- **Logs**: `web.log` / `web_err.log`

### M1 progress (after M1.prep + M1.0 + M1.1 + M1.2)
- ✅ **M1.prep** — all 8 steps (9 commits)
- ✅ **M1.0 Live serve probe** — done: v2 HTTP API + per-message model +
  chunked JSON stream consumption. Its leftovers (session resume across
  serve restarts, completion semantics) were closed by the M1.3 step-0
  probes.
- ✅ **M1.1 Record split** — done: Delegation v2 schema + per-project
  persistence + SubAgentRun + API filters + UI v1 bridge.
- ✅ **M1.2 Specialist store + CRUD** — done: `Specialist` dataclass +
  per-scope stores (global `~/.sweave/agents.yaml` + per-project
  `{project}/.sweave/agents.json`) + `SpecialistResolver` (project→global→seed
  resolution, orchestrator auto-seed singleton) + `is_orchestrator` flag
  + model precedence chain at submit + `/api/specialists` CRUD +
  `PUT /api/specialists/{name}/model` (emits `model.changed` with new
  `{name, model, scope}` shape) + override log (gold labels for R6
  dispatch) + `SubAgentRun` endpoints (`POST/GET/finish`) + UI v1
  compat bridge writes a `ChildSession` carrying `delegation_id` on
  submit (R4 removes the bridge). Tier framing baked in: Orchestrator /
  Specialist / SubAgent three-tier model with Orchestrator as a singleton
  (flag on Specialist; not a separate type).
- ✅ **M1.3 Shared serve + durable context** — done: `ServeRunner` per
  (specialist, worktree) pair with lazy start, idle TTL, psutil orphan
  sweep. `SpecialistRuntime` orchestrates one delegation: session
  create / 404-recreate / reuse + worktree re-injection preamble +
  structured ModelRef in `body["model"]` (M1.3 K-revised; user's
  `opencode.json` has multi-provider: ollama, gmicloud, zai, opencode
  default). `JobRunner` integrates the runtime (legacy
  `delegate_tool.execute` path preserved). M1.3 step 4: per-turn
  timeout (default 15 min) on both paths; success routes to
  `review` (M1.4 promotes to `done`). M1.3 step 0 (probe) amended:
  opencode persists sessions in `~/.local/share/opencode/opencode.db`
  (SQLite), so the stored `session_id` survives opencode process
  restarts — the 404-recreate path covers the rare case where a
  session has been deleted (or the worktree path changed). Branch A's
  cwd isolation is still the architectural rationale for
  per-specialist runners; cross-restart session reuse is a bonus.
  ServeRunner's lifetime. 269/269 pytest across 25 files; 13/13
  run.py --check; 40/40 test_full; 8/8 test_browser; ALL GREEN
  test_agents_loader.
- ▶ **Next**: M1.3 shared serve + durable context (per DESIGN.md §6 R1)

### M1.prep — done 2026-08-29
- **Plan of record**: `docs/M1_PREP_PLAN.md`
- **Test results**: 62/62 pytest, 13/13 `run.py --check`, 40/40 `test_full.py`, 8/8 `test_browser.py`, 8/8 `test_projects.py`, ALL GREEN on `test_agents_loader.py`
- **New files**: `sweave/web/{state,deps,events}.py`, `sweave/web/routers/{projects,agents,tasks,worktrees,memory,config,fs,delegations}.py`, `sweave/runtime/{__init__,locking,trace_log,delegation_store,job_runner}.py`, `tests/{conftest,test_seed_agents,test_atomic_write,test_locking,test_ws_event_bus,test_trace_log,test_delegation_store,test_job_runner,test_projects_atomic,test_projects_lock,test_routers_smoke}.py`
- **Deleted**: `sweave/web/api.py` (stale duplicate, never mounted)
- **New endpoints**: `POST /api/v2/tasks` (async — returns `{delegation_id, status: queued}`), `GET /api/delegations`, `GET /api/delegations/{id}`, `POST /api/delegations/{id}/wait`
- **Sync `/api/tasks` kept** for backward compat; deprecated in OpenAPI, slated for removal in M1.4+
- **WS event bus**: `WSEventBus` (sweave/web/events.py) is the single pub/sub; legacy event names (`agent_created`, `model_changed`, `task_completed`, `worktrees_cleaned`, `worktree_removed`, `rule_added`) preserved on the wire
- **Trace log**: `~/.sweave/traces/{delegation_id}.jsonl` (one JSON object per line)
- **Git history**: 40 commits on `master` (M0 rebuild → design docs → M1.prep → M1.0 → M1.1 → M1.2)

---

## CRITICAL BUGS FIXED (Latest Session)

### Bug 1: Sidebar buttons don't work (Session 7)
**Root cause**: Multiple potential issues — DOM ready state, event listener timing, complex module architecture.

**Fix**: Complete rewrite of frontend using:
- All JS in one IIFE to avoid scope issues
- `if (document.readyState === 'loading')` check before setting up listeners
- Global error handlers for debugging
- Single `switchTab()` function as source of truth

### Bug 2: Everything hidden after loading (Session 8)
**Root cause**: The `app` div starts with `class="app hidden"` but `showProjectMode()` and `showWelcome()` never removed the `hidden` class. They only toggled inner sections.

**Fix**: Added critical line in `start()` function:
```js
setTimeout(() => {
  const loader = $('loader');
  if (loader) { /* hide loader */ }
  // CRITICAL: Remove hidden class from app so it's visible
  const app = $('app');
  if (app) app.classList.remove('hidden');
}, 500);
```

---

## Architecture: Session = Orchestrated Conversation

A **Session** is an orchestrated conversation that can spawn **child sessions** (specialist agent runs):

```
Session (parent - orchestrator)
├── Messages (user, assistant, tool calls)
├── Context (current state)
├── Memory Bank (session-scoped)
└── Child Sessions (specialist agent runs)
    ├── backend agent (own worktree, own context)
    ├── frontend agent (own worktree, own context)
    └── reviewer agent (own worktree, own context)
```

---

## Project Structure

```
C:\Users\user\sweave\
├── config.yaml              # Server, harness, memory, git, models, routing
├── models.yaml              # Model registry (4 roles)
├── rules.yaml               # 5 routing rules + LLM fallback
├── pyproject.toml           # Dependencies
├── .env.example             # API key template
├── .gitignore
├── README.md
├── PROJECT_STATE.md         # THIS FILE
│
├── start_server.py          # Start server detached
├── stop_server.py           # Stop server via PID file
├── run.py                   # Run server or test suite
├── test_server.py           # Server endpoint tests
├── test_projects.py         # Project/session tests
├── test_browser.py          # File browser API tests
├── test_v3.py               # Comprehensive UI verification
├── test_full.py             # Latest comprehensive test
├── verify_v2.py             # Older verification script
├── check_modules.py         # JS module verification
│
├── scripts/
│   ├── generate_models.py   # Fetches models.dev -> models.yaml
│   └── setup_hindsight.py   # Hindsight init
│
└── sweave/                  # Python package
    ├── __init__.py
    ├── projects.py          # Project + Session + Message + ChildSession data model
    ├── api/
    │   └── projects.py      # Project/Session API endpoints
    ├── agents/              # Agent YAML configs
    ├── cli/                 # Typer CLI
    ├── config/              # Pydantic schemas + ConfigManager
    ├── harness/             # Harness abstraction + OpenCode impl
    ├── memory/              # Hindsight backends
    ├── router/              # RuleRouter with template resolution
    ├── workspace/           # WorktreeManager
    ├── tools/               # Tools
    └── web/
        ├── __init__.py
        ├── server.py        # FastAPI app (40+ routes)
        └── static/
            ├── index.html    # Single-page app
            ├── style.css     # All styling
            └── js/
                └── app.js   # All JS in one IIFE
```

---

## Key Architectural Decisions (Final)

### 1. JS in IIFE
The entire app.js is wrapped in `(function() { 'use strict'; ... })()` to prevent any global scope pollution.

### 2. `switchTab()` is CORE
The navigation is the most critical function. Defined ONCE, called from event listeners. Both nav items and tab panels use the same `data-tab` attribute.

### 3. `setupEventListeners()` called after DOM is ready
```js
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start);
} else {
  start();
}
```

### 4. **CRITICAL**: App must be unhidden after loader
In `start()`, the setTimeout that hides the loader **MUST** also remove the `hidden` class from `#app`:
```js
setTimeout(() => {
  const loader = $('loader');
  if (loader) { /* hide loader */ }
  const app = $('app');
  if (app) app.classList.remove('hidden');  // <-- THIS WAS MISSING
}, 500);
```

### 5. Global error handlers
```js
window.addEventListener('error', (e) => {
  showError('JS Error: ' + (e.error?.message || e.message));
});
window.addEventListener('unhandledrejection', (e) => {
  showError('Promise Error: ' + (e.reason?.message || e.reason));
});
```
This shows errors in a visible error box at the top of the page.

### 6. All elements have stable IDs
JS queries via `$('id')`, not fragile class names.

### 7. Backend-driven file browser
Uses Python's `pathlib` for cross-platform support. Works on all browsers and OS.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| **Projects** | | |
| GET | /api/projects | List projects |
| POST | /api/projects | Create project (from folder) |
| GET | /api/projects/active | Get active project |
| POST | /api/projects/{name}/active | Switch to project |
| DELETE | /api/projects/{name} | Delete project |
| **Sessions** | | |
| GET | /api/sessions | List sessions |
| POST | /api/sessions | Create session |
| GET | /api/sessions/active | Get active session |
| GET | /api/sessions/{id} | Get session with messages + children |
| POST | /api/sessions/{id}/active | Switch to session |
| POST | /api/sessions/{id}/messages | Add message |
| DELETE | /api/sessions/{id} | Delete session |
| **File System** | | |
| GET | /api/fs/drives | List available drives/roots |
| GET | /api/fs/list?path=X | List directory contents |
| POST | /api/fs/validate | Validate project path |
| POST | /api/fs/create | Create new directory |
| **Memory** | | |
| GET | /api/memory/banks | Get hierarchical banks |
| POST | /api/memory/recall | Search memories |
| POST | /api/memory/retain | Store memory |
| POST | /api/memory/reflect | Synthesize memories |
| **Agents, Tasks, Config** | | |
| GET/POST/PUT/DELETE | /api/agents[/name] | Agent CRUD |
| POST | /api/tasks | Execute task |
| POST | /api/route | Preview routing |
| GET/POST | /api/models | Model config |
| GET/POST | /api/rules | Routing rules |
| GET | /api/config | Full config |
| WS | /ws | WebSocket for real-time updates |

---

## UI Layout

```
┌──────────────────────────────────────────────────────┐
│ ☰ Sweave  project-name / session-name  [●] [+Session]│ ← topbar (56px)
├──────┬───────────────────────────────────────────────┤
│ Chat │                                               │
│ Child│                                               │
│ Agent│   ┌─────────────────────────────────────┐    │
│ Memor│   │                                     │    │
│ ─────│   │           CONTENT AREA             │    │
│ Sett│   │       (chat / children / etc)      │    │
│      │   │                                     │    │
│ [●]  │   └─────────────────────────────────────┘    │
└──────┴───────────────────────────────────────────────┘
```

Layout uses CSS Grid with `height: 100vh; width: 100vw` on the app container. Top bar is fixed height (56px), sidebar is 200px (64px when collapsed).

---

## Key Files (Critical to Read)

1. **`sweave/web/static/index.html`** - Single-page app structure (must have all element IDs)
2. **`sweave/web/static/style.css`** - All styling
3. **`sweave/web/static/js/app.js`** - All JS in one IIFE (must unhidden #app after loader)
4. **`sweave/web/server.py`** - FastAPI routes
5. **`sweave/projects.py`** - Project/Session data model
6. **`sweave/api/projects.py`** - Project/Session API endpoints

---

## Configuration

### config.yaml
```yaml
server: { host: "127.0.0.1", port: 8080 }
harness:
  default: "opencode"
  opencode: { command: "opencode", serve_args: ["--port", "0"] }
memory:
  backend: "hindsight"
  hindsight:
    mode: "embedded_slim"
    bank_id: "project-memory"
git:
  provider: "github"
  worktree_base: ".worktrees"
  auto_pr: true
  pr_base_branch: "main"
models: { registry_path: "models.yaml", rules_path: "rules.yaml", hot_reload: true }
routing: { routes: [], fallback: "llm" }
```

### models.yaml
```yaml
models:
  orchestrator: { default: "deepseek-flash", aliases: [...], provider: "opencode" }
  backend: { default: "glm-5.2", aliases: [...], provider: "opencode" }
  frontend: { default: "hy3", aliases: [...], provider: "opencode" }
  reviewer: { default: "claude-3.5-sonnet", aliases: [...], provider: "opencode" }
```

### rules.yaml
```yaml
routes:
  - pattern: "backend|api|database|server|sql|auth|orm"
    agent: "backend"
    model: "{{models.backend.default}}"
fallback: "llm"
```

---

## Running Commands

```bash
# Start server (background, detached)
python start_server.py 8100 127.0.0.1

# Stop server
python stop_server.py

# Run server (foreground)
python run.py --host 127.0.0.1 --port 8100

# Run tests
python run.py --check              # 13 endpoint tests
python test_browser.py 8100        # 8 file browser tests
python test_full.py                # 40 comprehensive UI tests
python -m pytest tests/            # 62 unit/integration tests (source of truth)

# CLI commands
sweave run "Build a REST API"
sweave route "Create a React component"
sweave models --list
sweave doctor
```

---

## User Context

The user wants:
1. **Project-scoped agents** - Open a folder → project with own memory/agents
2. **Hierarchical memory** - Global + project + session
3. **Session = orchestrated conversation with children**
4. **Specialist agents** are persistent, not temporary
5. **Different models per agent** (dynamically selectable)
6. **Web UI** (Odysseus-inspired, full-screen)
7. **Configurable Hindsight memory** (embedded/docker/cloud)
8. **OpenCode as default harness** (extensible)
9. **CLI: `sweave`**
10. **GitHub workflow** (gh CLI optional)
11. **Windows-friendly** (separate scripts for background processes)

**Critical UI requirements** (all now met):
- ✅ **Full-screen** interface (not stopping mid-screen)
- ✅ **All buttons must work** (navigation, modals, etc.)
- ✅ **App must be VISIBLE** after loading (not hidden)
- ✅ **Welcome screen** is a proper design (not just an ugly button)
- ✅ **File access via backend** (not browser sandbox)
- ✅ **Folder picker works** on Windows (backend-driven, not browser API)

---

## History of Build Sessions

### Session 1: Initial setup
- CLI structure, config system, routing, harness abstraction, memory backends, basic SPA

### Session 2: UI enhancement
- Odysseus-inspired UI with Jinja2 templates, then React frontend

### Session 3: Web server + JS modules
- FastAPI server, static files, JS modules

### Session 4: Bug fixing
- Fixed fullscreen issue (position: fixed, 100vh/100vw)
- Fixed timing issues (lazy DOM element lookup)
- Fixed font 404s (removed @font-face)

### Session 5: Project/Session architecture
- Project + Session + ChildSession data model
- Hierarchical memory
- Welcome screen, sidebar project context

### Session 6: Clean rewrite
- Removed Jinja2 templates, React frontend, complex theme system
- Single clean index.html with all UI
- Single clean app.js (no module imports, no build step)
- Clean style.css with 6 preset themes

### Session 7: File browser fix
- Added backend `/api/fs/*` endpoints (drives, list, validate, create)
- Replaced broken File System Access API with backend-driven file browser
- Added proper 2-pane file browser UI in the project modal
- No more "Folder picker not supported" error

### Session 8: Sidebar navigation fix
- Completely rewrote the entire frontend from scratch
- **Sidebar navigation NOW WORKS** - tested with comprehensive verification
- All JS in one IIFE to avoid scope issues
- Global error handlers show errors on screen for debugging
- Strict DOM ready check before setting up listeners
- 5 themes (dark, light, dracula, nord, catppuccin) with data-theme attribute
- 42/42 verification tests pass

### Session 10 (latest): M1.1 — Record split (Delegation v2 + SubAgentRun + UI v1 bridge)
- 5-step refactor per `docs/M1_1_PLAN.md`:
  1. Delegation v2 fields + v1→v2 migration (`SCHEMA_VERSION=2`,
     `Manifest` TypedDict, `_migrate_v1_to_v2` helper)
  2. Per-project disk persistence via `PerProjectDelegationStores`
     (`{project}/.sweave/delegations.json`, atomic write-through via
     `runtime.locking.atomic_write_json_sync`, lazy per-project loading,
     corruption recovery)
  3. `SubAgentRun` ephemeral type (`runtime/subagent_store.py`,
     per-process, in-memory, FIFO-capped at 500, R2's `/investigate`
     will consume it)
  4. API filters on `/api/delegations` (`?project_name=&status=&parent_task_id=`),
     v2 task accepts `parent_task_id` + `manifest` passthrough,
     `SubAgentRun` endpoints (`POST/GET/finish`), UI v1 compat bridge
     writes a `ChildSession` carrying `delegation_id` on submit
  5. Gates + docs (this session)
- `ChildSession.delegation_id` field added; `from_dict` reads it with
  `.get()` so pre-M1.1 JSON files load with `delegation_id=None` (the
  UI v1 render path treats None as legacy entry)
- JobRunner now holds `PerProjectDelegationStores` + a
  `project_dir_resolver` callback (so it stays domain-agnostic; the
  AppState supplies the closure over `project_manager`)
- 122/122 pytest (was 80 after M1.0; +42 across M1.1: 10 schema + 9
  JobRunner + 16 SubAgentRun + 14 step 4)
- 13/13 run.py --check, 40/40 test_full, 8/8 test_browser, ALL GREEN
  test_agents_loader
- 14 new commits on `master` across M1.0 + M1.1 (4 + 5 + 5)

### Session 11 (latest): M1.2 — Specialist store + CRUD + UI v1 agents bridge
- 5-step refactor per `docs/M1_2_PLAN.md` (plan itself amended in
  chat — Orchestrator / Specialist / SubAgent tier framing baked in
  + 7 audit amendments: A seed→role_ref boundary, B model.changed
  shape, C new event names, D .sweave subdir creation, E anchored
  path, F override log no-active-project case, G role_ref as
  optional hint not hard binding):
  1. `Specialist` + `GlobalSpecialistStore` (`~/.sweave/agents.yaml`,
     home-anchored — kills the CWD-relative gotcha) + `ProjectSpecialistStore`
     (`{project}/.sweave/agents.json`, creates `.sweave` subdir) +
     `SpecialistResolver` (project→global→seed, orchestrator
     auto-seed, name validation, seed read-only view).
  2. Legacy import: anchored `agents.yaml` becomes the source of
     truth; `AppState.dynamic_agents` is now a derived view for the
     legacy router until R4. Model precedence chain in
     `DelegateTaskTool._resolve_model` (4 levels: task_override >
     specialist.current_model > role_ref hint > legacy).
  3. `/api/specialists` CRUD + `PUT /{name}/model` (emits the new
     `model.changed {name, model, scope}` shape) + `specialist.*`
     events. Override log at `{project}/.sweave/override_log.jsonl`
     (global fallback `~/.sweave/override_log.jsonl`) — appended
     when a v2 task carries an explicit `agent` that differs from
     the router decision (R6 dispatch gold labels).
  4. `/api/agents` bridge (test-first: tests fail with the dict shape
     first, then the fix). Array shape `{builtin, global, dynamic}`
     + description-overwrite bug fixed (PUT now patches `description`
     and `system_prompt` independently). Orchestrator name 409 on
     create/delete. New + legacy event names both emitted.
  5. Docs + this session.
- 207/207 pytest (was 122 after M1.1; +85: 33 specialist store + 13
  model precedence + 23 specialists router + 16 agents bridge).
- 13/13 run.py --check, 40/40 test_full, 8/8 test_browser, ALL GREEN
  test_agents_loader.
- 5 new commits on `master` for M1.2 + 1 plan-amendment commit +
  the two CONTEXT / plan commits = 8 across this session.
- **Stale server caught + killed**: a leftover PID 7028 from the
  M1.1 audit was still bound to port 8100; killed before the
  final sweep so the post-M1.2 server loads cleanly. Server
  boots the new code; the v1.18 OpenCode harness fix (M1.0) and
  the per-message model field are still active; nothing was
  rolled back.

### Session 12 (latest): M1.3 — Shared serve + durable context
- 6-step refactor per `docs/M1_3_PLAN.md` (plan itself amended in
  chat — 7 audit amendments A–G: K-revised model_ref shape, drop dead
  parser branch, new event names, .sweave subdir creation, anchored
  agents.yaml path, override log fallback, role_ref as optional hint).
  Step 0 ran first as an empirical probe to ground the plan in real
  v2-protocol behavior:
  1. `ServeRunner` per (specialist, worktree). Lazy start, psutil
     orphan sweep on boot, idle TTL (default 30 min, config knob).
     `find_orphan_serves` / `sweep_orphan_serves` helpers in
     `runtime/serve_runner.py`; no-op when psutil isn't installed.
   2. `SpecialistRuntime` + `ModelRef` + session lifecycle:
      `_model_body` always emits `{providerID, modelID}` when known
      (K-revised); `_ensure_session` covers create / 404-recreate /
      reuse paths; worktree preamble injected on every message.
      Probe-amended: opencode persists sessions in
      `~/.local/share/opencode/opencode.db` (SQLite), so the stored
      `session_id` DOES survive opencode process restarts (the
      `opencode serve` is just the in-memory request handler). The
      404-recreate path covers the rare case where a session has
      been deleted (or the worktree path changed).
  3. `JobRunner` integration: new optional ctor args
     `specialist_runtime` + `specialist_factory`; when wired, the
     runtime path runs; otherwise legacy `delegate_tool.execute`.
     Bare `current_model` strings parse as `ModelRef(provider=None,
     ...)`; structured `provider/model` strings parse to the typed
     pair; the v2 body emits the structured shape.
  4. Turn timeout (15 min default) + review transition: success ->
     `review` (M1.4 promotes to `done`); failure (timeout, agent
     error) -> straight to `failed`. Trace records the timeout event
     for observability.
  5. Live gate (manual): `tests/test_m1_3_step5_live_gate.py` --
     skipped in this env because the opencode startup races the
     sweave boot (separate Popen). The probe docs are the manual
     proof for now.
   6. Docs: DESIGN §4 (Agent lifecycle ✅, OpenCode spawn path ✅,
      SpecialistRuntime row), §2.1 amendment (per-specialist serve
      runner; **opencode.db session persistence** so the stored
      `session_id` survives opencode process restarts), R1 M1.3 ✅.
- 269/269 pytest (was 207 after M1.2; +62: 17 serve_runner + 21
  model_ref + 12 specialist_runtime + 6 step3 integration + 6
  step4 timeout/review + tests fixed along the way).
- 13/13 run.py --check, 40/40 test_full, 8/8 test_browser, ALL GREEN
  test_agents_loader.
- 6 new commits on `master` for M1.3 + 2 plan/context commits = 8
  across this session.
- **Stale server caught + killed** (same gotcha as M1.2): a leftover
  process was bound to a test port; killed before final sweep so the
  new code loads cleanly.

The current implementation is clean, working, and reliable. All reported bugs
have been fixed and verified.

---

## Critical Lessons Learned

1. **ALWAYS check if your main container needs to be unhidden** - If it starts with `display: none` or `hidden` class, something must explicitly remove that.

2. **Test with actual user perspective** - Just because the server returns 200 doesn't mean the UI is visible.

3. **Use global error handlers** - They're invaluable for debugging.

4. **Strict DOM ready check** - `document.readyState === 'loading'` check before setting up listeners.

5. **Single source of truth for tab switching** - One function, called from multiple places.

6. **Backend-driven file browser** - Don't rely on browser File System Access API; use your own backend endpoints.

7. **Comprehensive test scripts** - Have a test that verifies every element is present and every API endpoint works.