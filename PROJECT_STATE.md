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

> **Design**: `DESIGN.md` (architecture, component status, roadmap R0–R5).
> **Agent guide**: `AGENTS.md`. UI v2 plan is folded into DESIGN.md §6 R4.

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

### Test Results (All Passing)
- **40/40** in `test_full.py` (comprehensive UI verification)
- **13/13** in `run.py --check` (core endpoints)
- **8/8** in `test_browser.py` (file browser API)

### Currently Running
- **Web server**: PID 16092 on `http://127.0.0.1:8100`
- **PID file**: `C:\Users\user\sweave\web.pid`
- **Logs**: `C:\Users\user\sweave\web.log` and `web_err.log`
- **Stop with**: `python stop_server.py`
- **Start with**: `python start_server.py 8100 127.0.0.1`

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

### Session 9 (latest): App visibility fix
- **Bug**: Everything was hidden after loading
- **Root cause**: The `app` div starts with `class="app hidden"` but `showProjectMode()` and `showWelcome()` never removed the `hidden` class
- **Fix**: Added `app.classList.remove('hidden')` in the setTimeout that hides the loader
- **Result**: 40/40 tests pass, app is now visible

The current implementation is clean, working, and reliable. All reported bugs have been fixed and verified.

---

## Critical Lessons Learned

1. **ALWAYS check if your main container needs to be unhidden** - If it starts with `display: none` or `hidden` class, something must explicitly remove that.

2. **Test with actual user perspective** - Just because the server returns 200 doesn't mean the UI is visible.

3. **Use global error handlers** - They're invaluable for debugging.

4. **Strict DOM ready check** - `document.readyState === 'loading'` check before setting up listeners.

5. **Single source of truth for tab switching** - One function, called from multiple places.

6. **Backend-driven file browser** - Don't rely on browser File System Access API; use your own backend endpoints.

7. **Comprehensive test scripts** - Have a test that verifies every element is present and every API endpoint works.