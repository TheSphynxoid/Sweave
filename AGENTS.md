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

## How we plan — planning session method

The planning session's job is to **produce and keep current the plan of record**
(`docs/M{X}_PLAN.md`) for the *next* execution session — while execution happens
in parallel elsewhere. Planning sessions do not implement.

**Steps** (the loop; one round per user check-in):
1. **Re-read reality first.** `git log`/`git status` (execution may have advanced
   under you), run the gates (`pytest`, smoke, UI), and spot-check what the
   executor's docs claim. Trust is earned per claim: test counts, probe results,
   and "done" markers are verified, not assumed.
2. **Consistency audit.** Reconcile plans, `DESIGN.md`, `PROJECT_STATE.md`, and
   `AGENTS.md` against the code. Classify findings: *accurate* / *stale-fix-now* /
   *amend-plan* / *ask-user*. Fix stale cross-refs immediately (small commit);
   never let the context docs contradict the code for a full round.
3. **Report the state** to the user compactly: what landed, what drifted, what
   you verified, what contradicts what.
4. **Discuss forks with the user, get rulings.** Present options as tables with
   trade-offs and a recommendation. Rulings are user-locked, dated, and written
   into the plan + `DESIGN.md` (not just chat). Questions of principle (merge
   policy, promotion policy, LLM-context ownership) always go to the user.
5. **Detail the next step** into `docs/M{X}_PLAN.md`: *Starting point* (what
   exists now — re-verify against code, not against older plan bullets), *goal
   state*, numbered steps with estimates and done-gates, *explicit non-goals*,
   *risks*. A plan re-scopes against what previous milestones actually built;
   stale assumptions are the planner's failure, not the executor's.
6. **Absorb executor amendments.** Execution sessions may amend plans with
   justification (their right, per the execution method). The planning session
   audits each amendment for consistency, reconciles the context docs if the
   amendment changed architecture, and treats a well-argued deviation as design
   progress — the converged plan is better than the original plan.
7. **Update shared context before stopping**: rulings, known issues (e.g. flaky
   tests), and the next plan's location surfaced in `PROJECT_STATE.md`; new
   adoptions in `DESIGN.md` §8; gotchas in this file. Everything committed —
   the next session must be able to pick up losslessly from the repo alone.

**The loop per round**: state → consistency → discuss → confirm → detail →
commit. If a round finds nothing to plan, say so — don't invent work.

**The planning audit targets execution, not its own design.** When the planner
audits, it asks: did the executor stick to the plan? Were deviations justified
and recorded as plan amendments? Do the gate counts / test counts / "done"
markers match what the executor claimed? Did the design hold? The planner
does *not* re-derive the plan's design decisions — that's already locked in
the plan + rulings. The planner audits *execution against the plan*.

## How we work — execution session method

The execution session's job is to **execute** a milestone plan that was agreed
in a separate planning session. The plan lives at `docs/M1_N_PLAN.md` (or
equivalent); the execution session reads it, refines it through discussion when
details are ambiguous, and ships the step commits.

**The execution session does NOT write the plan.** If the plan is missing,
incomplete, or wrong, surface that to the user; don't try to be the planner.
The planning session's method is the section above.

**Steps**:
1. **Read the prior state** (plan + `DESIGN.md` + `PROJECT_STATE.md` + this file's
   gotchas + the prior plan's commit log + the relevant code). The plan's
   "Starting point" section tells you what to read.
2. **Check the plan for coherence and consistency against current state.**
   Run a consistency audit before executing: verify the "starting point"
   still holds; the rulings still hold; the file paths and module names
   still match; the test counts in the gate still match; the explicit
   non-goals still hold. Classify every finding as *accurate* /
   *amend-plan* / *ask-user* and report with file:line evidence. If
   anything is inconsistent, surface the discrepancy to the user before
   executing. Don't silently execute against a stale plan.
3. **Confirm the plan's design with the user; ask for clarification on details.**
   The plan is a living document — refine it through discussion when details
   are ambiguous or the planning session didn't cover something. The execution
   session refines; it doesn't redesign the milestone's architecture.
   If execution justifies a deviation from the plan, **amend the plan**
   (user-locked: "if you deviated from the plan, and you can justify it,
   then amend the plan") — document the deviation in the plan's own
   amendment/deviation section, and keep it to one per milestone where
   possible.
4. **Execute the steps in order, with the agreed refinements.** Each step has
   its own commit, named for the step (e.g. "M1.7 step 1: ..."). Tests first
   when the plan calls for it. Run the step's gate (tests + run.py + test_full
   + live check when applicable) before moving to the next step. If a step's
   gate fails, fix it before moving on. If a step requires a user decision,
   ask; don't assume.
5. **Update the docs at the end.** `DESIGN.md` (§4 component status, R1 bullet,
   §2.1 notes if the plan changed the architecture). `PROJECT_STATE.md`
   (M-number progress + rulings + post-execution summary). This file (gotchas
   for things that bit during execution). The plan file itself: bump the
   "Status" header from "planned" to "done" with the date and a one-line
   summary; add a final "Execution summary" section if the plan grew
   significantly during execution.
6. **Stop and report.** Final state: which steps landed, which (if any) were
   deferred, what the test count is, what the live gate showed, what gotchas
   landed. Wait for the user's next instruction.

**The execution audit targets the plan, not the plan's design.** When the
executor audits in step 2, it asks: does the *starting point* still hold
against the code? Do the rulings still bind? Do the file paths and module
names still match? Do the test counts in the gate still match? Do the
explicit non-goals still hold? The executor does *not* re-derive the plan's
design decisions (e.g. "should chat turns auto-done?" — the planner already
ruled) and does *not* re-audit the executor's previous execution (that's the
planner's job in the next round). If the audit finds design-level issues
the planner didn't consider, surface them as *ask-user*, not as executor
amendments.

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
10. **MCP auth token is the only seam between the sweave MCP server
    and the API** (M1.6). Both the stdio server (`sweave/mcp/`)
    and the API router (`/api/mcp/specialists`) read the shared
    token at `~/.sweave/mcp_token` (auto-generated on first call,
    mode 0o600 best-effort). The token is **localhost-only** -- it
    is the only thing standing between the MCP surface and the
    `/api/mcp/specialists` endpoint. If you're writing an
    end-to-end test that drives the MCP path, **always** set
    `Path.home` to a temp dir (the `mcp` token path derives from
    `Path.home()` and you don't want tests to overwrite the
    real user's token). The TestClient fixtures in
    `tests/test_m1_6_step1_mcp_server.py` and
    `tests/test_m1_6_step3_orchestrator_wiring.py` already do this.
    The token is also passed through `SWEAVE_MCP_TOKEN` env var
    (set by the per-project opencode.json `environment` block at
    M1.6 step 3) so the opencode-spawned subprocess can read it
    without reading the home file directly.
11. **Per-project opencode.json plumbing is idempotent** (M1.6). The
    `ensure_mcp_config(project_dir)` call writes (or refreshes) the
    project's `opencode.json` so the orchestrator's serve session
    in that cwd sees the sweave MCP server. The `_sweave_managed`
    marker inside the `mcp.sweave` entry is the contract: presence
    = sweave wrote it, absence = user wrote it (we leave it alone).
    If you need to add fields to the sweave MCP entry, update
    `_sweave_mcp_entry` in `runtime/mcp_config.py`; the function
    is the single source of truth. The `M1.6_DISABLE_MCP_PLUMBING=1`
    env var is the test/CI kill switch.
12. **Delegation schema-version field gate** (M1.7). The
    `test_v3_field_set_includes_all_m1_1_plus_m1_6_fields` test in
    `tests/test_delegation_store.py` pins the public Delegation
    field set -- any new field requires bumping `SCHEMA_VERSION` in
    `runtime/delegation_store.py` AND adding a migration helper
    (`_migrate_vN_to_vN+1`) that defaults the new field for older
    records. The plan for M1.7 step 2 said "no schema bump" for the
    `kind` field; that reasoning was wrong -- the gate is real.
    Bumping 3 → 4 + `_migrate_v3_to_v4` was the correct fix. The
    same rule applies for the Session schema: new fields
    (`orchestrator_session_id` in step 1; `last_memory_recall_ts` +
    `last_git_snapshot` in step 4) get `data.get("...", default)` in
    `Session.from_dict`, so legacy session files load without a
    migration helper. The Delegation dataclass has stricter
    shape-pinning; check both before adding a field.
13. **Chat loop auto-done is the chat path's job, not JobRunner's**
    (M1.7 step 3). `JobRunner._run` sets `final_status = "review"`
    on success for implementation delegations (M1.4+M1.5 ruling).
    ChatLoop overrides the chat delegation to `"done"` after the
    final reply -- the chat path is the only place that knows it
    should be `done`. Implementation children of a chat turn still
    stop at `review` independently. If you wire a new orchestrator
    surface (e.g. a `/investigate` API), keep the same split: the
    surface that owns the lifecycle owns the auto-done.
14. **Runtime transcript composer is the runtime's view, not the
    engine's** (M1.7 step 4). The composed prompt is what the
    LLM sees from the runtime; the engine (opencode today) also
    carries its own session memory on top. The "LLM is a consumer
    of what the runtime builds" framing is about the runtime's
    contribution only. The sweave-internal engine (side-project,
    future) is where the runtime fully owns the transcript. If you
    add a new engine, decide: external (composed + engine view) or
    internal (composed only) -- the engine's transcript
    compatibility is your call.
15. **Stale server blocks `start_server.py` silently** (M1.8
    step 4). When running a live scene with `SWEAVE_MOCK_OPENCODE=1`,
    a previous `python start_server.py` invocation that wasn't
    stopped can leave port 8100 bound. The new `start_server.py`
    will print "Server started" and write a PID, but the new
    server will fail to bind (you'll see "ERROR: [Errno 10048]
    only one usage of each socket address" in `web_err.log`).
    The chat runtime will then hit a real opencode on
    `127.0.0.1:4096` instead of the mock, producing a 500 in
    the assistant message and an HTTPStatusError traceback. The
    M1.8 live scene's first run hit this. Always run
    `python stop_server.py` before `start_server.py` and verify
    `Get-NetTCPConnection -LocalPort 8100` is empty (or use
    `Test-NetConnection 127.0.0.1 -Port 8100` to confirm the
    port is free). The mock-only behavior is gated on the env
    var being set in the server's environment, not the
    script's -- a stale server without the env will silently
    consume the test.

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
