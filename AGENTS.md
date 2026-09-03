# AGENTS.md — Guide for AI agents working in this repo

Read this first. Then read `DESIGN.md` (architecture + roadmap) and `PROJECT_STATE.md`
(runtime state + build-session history). If facts conflict, code wins — then update docs.

## What this project is
Sweave: standalone Polly-style multi-agent orchestrator (see DESIGN.md §1). Python 3.11+,
FastAPI backend, vanilla-JS no-build SPA, OpenCode as first harness. Windows-first.

## Ground rules
- **Never merge PRs, never `git merge`/`gh pr merge`** — that's the product's own rule;
  honor it when working on Sweave too.
- The repo had **zero commits** until 2026-08-29. Commit early, small, descriptive.
  Never amend a pushed commit.
- No build step for the web UI: no npm/bundler in `sweave/web/static/`. Vanilla ES6 only.
- Python style: ruff line-length 100, pydantic v2, `from __future__ import annotations`.
- New dependency? Check DESIGN.md §8 policy first: small, maintained, permissive license;
  no framework that owns the agent loop. Record every adoption in §8.

## Key files
| Path | What |
|---|---|
| `sweave/web/server.py` | FastAPI app, 40+ routes, SPA serving |
| `sweave/web/static/{index.html,style.css,js/app.js}` | entire UI; app.js = one IIFE |
| `sweave/tools/__init__.py` | DelegateTaskTool (task→worktree→spawn), RouteTaskTool, MemoryTool |
| `sweave/harness/{base,opencode,detect}.py` | AgentSpec/AgentProcess protocol; OpenCode impl |
| `sweave/workspace/manager.py` | git worktrees `.worktrees/{task_id}/{agent}` + PR |
| `sweave/projects.py` + `sweave/api/projects.py` | Project/Session/ChildSession model + endpoints |
| `sweave/config/` | pydantic schemas, ConfigManager (hot reload) |
| `sweave/memory/backends.py` | hindsight embedded/docker/cloud |
| `sweave/agents/*/config.yaml` | Omnigent-spec agent defs (loader planned R0 — currently DEAD) |

## Known gotchas (burned us once — don't relearn)
1. `start_server.py`/`stop_server.py` resolve `web.pid` **relative to CWD** — run them
   from the repo root only, or they silently no-op while an old server keeps serving.
   (M1.2 lesson: a stale `python` process left running on port 8100 silently blocks
   `start_server.py` from binding; the new server is killed before its lifespan runs.
   Always verify `python` processes are gone after `stop_server.py` succeeds, or kill
   by PID.)
2. `ProjectManager` is **AppState-owned and loaded once at server startup**
   (M1.prep lifespan, not an import-time singleton anymore) — editing
   `~/.sweave/config.json` while the server runs still does nothing until restart.
3. The M1.prep-era `agents.yaml` was **CWD-relative** (`Path("agents.yaml")`) —
   M1.2 anchored it to **`Path.home() / ".sweave" / "agents.yaml"`** (via
   `AppState._anchored_agents_path`). Code that wants the file should
   read `state.dynamic_agents_path` (which carries the anchored path) rather
   than constructing a new `Path("agents.yaml")` from CWD.
4. **Opencode sessions persist in `~/.local/share/opencode/opencode.db`
   (SQLite)** (corrects the M1.3 step 0 probe — that probe's
   "0 new files" finding was misleading: it saw the pre-existing file
   before and after but missed that a new row was inserted into the
   `session` and `message` tables). The `session_id` we persist on
   the `Specialist` record survives opencode process restarts (the
   SQLite DB is persistent; the `opencode serve` is just the
   in-memory request handler). The 404-recreate path in
   `_ensure_session` covers the rare case where a session has been
   deleted (or the worktree path changed). The worktree re-injection
   preamble is still required (cwd binds to the serve process; sessions
   are tied to the cwd at create time via `message.path.cwd`).
5. **Opencode multi-provider config** (M1.3 K-revised). The user's
   `opencode.json` declares custom providers (ollama, gmicloud, zai,
   opencode default). The v2 `POST /session/{id}/message` body requires
   `body["model"]` to be either `null` or a structured
   `{providerID, modelID}` object — **bare model names are rejected
   with 400** (probe 5b). The harness always emits the structured pair
   when the `ModelRef` is complete; the v1 record's bare-string path
   falls back to the unqualified name (with a warning). If you add a
   new provider or rotate the catalog, run `opencode models` to see
   the canonical names.
6. UI: `#app` starts `hidden`; the loader timeout in `start()` MUST remove it
   (app.js:930ish). Welcome mode adds `.hidden` (display:none !important) to all `.tab`
   panels — `switchTab()` gating lives at the top of app.js. Don't "simplify" it away.
7. The server must be **restarted** to pick up static file changes? No — static is served
   from disk, but Python changes need restart; users must hard-reload (Ctrl+Shift+R).
8. First-match routing: rules.yaml order matters; templates resolve against the config
   object via dotted path (router.py `_resolve_template`).
9. **Runtime test files that drive `SpecialistRuntime.run` end-to-end MUST
   set `SWEAVE_MOCK_OPENCODE=1`** (the existing seam) via a module-scoped
   autouse fixture. Without it, `ServeRunner.start()` spawns a real
   `opencode serve` subprocess, which occasionally fails to bind
   (`RuntimeError: opencode serve exited early`) under full-suite load
   and turns the test order-dependent. The fixture is already wired
   in `tests/test_m1_3_step3_job_runner_integration.py`,
   `tests/test_m1_4_5_step1_model_ref_contract.py`, and
   `tests/test_m1_4_5_step2_switch_semantics.py`. A regression test
   (`test_runtime_runner_is_mocked_no_real_subprocess`) pins the
   invariant: removing the fixture makes the test fail immediately.
   If you add a new runtime-path test file, copy the fixture from
   one of those three.

## Running & testing
```bash
python start_server.py 8100 127.0.0.1   # from repo root; logs web.log / web_err.log
python stop_server.py                   # ditto
python run.py --check                   # 13 endpoint tests
python test_full.py                     # 40 UI checks
node test_sidebar_nav.js                # needs: npm i playwright-core (temp dir), Edge headless
powershell -File run_sidebar_regression.ps1   # full nav regression, restores server state
```
UI regression pattern: headless Edge via `playwright-core` (`channel: 'msedge'`), assert
`offsetParent` visibility (not just classList), capture console + pageerror. Write the
test before the fix; watch it fail; then fix.

## When you change the design
Update DESIGN.md roadmap/status table in the same change. Update PROJECT_STATE.md's
"Current State" when behavior (not just code) changes. Keep the component status table
honest: ✅ wired / ⚠️ stub / ❌ dead / 📐 planned.
