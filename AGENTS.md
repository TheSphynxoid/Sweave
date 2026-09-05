# AGENTS.md — Guide for AI agents working in this repo

Read this first. Then read `DESIGN.md` (architecture + roadmap) and `PROJECT_STATE.md`
(runtime state + build-session history). If facts conflict, code wins — then update docs.

## What this project is
Sweave: standalone Polly-style multi-agent orchestrator (see DESIGN.md §1). Python 3.11+,
FastAPI backend, React SPA (`sweave-web/`), OpenCode as first harness. Windows-first.

## Ground rules
- **Never merge PRs, never `git merge`/`gh pr merge`** — that's the product's own rule;
  honor it when working on Sweave too.
- The repo had **zero commits** until 2026-08-29. Commit early, small, descriptive.
  Never amend a pushed commit.
- The UI is `sweave-web/` (Vite + React 18 + TS + Tailwind + Zustand + React Query); the
  v1 vanilla UI (`sweave/web/static/`) is retired (2026-09-05). Run
  `cd sweave-web && npm run build` after UI changes; `dist/` is served by the backend's
  SPA catch-all and is NOT committed (gitignored).
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
   adoptions in `DESIGN.md` §8; new gotchas in `docs/GOTCHAS.md`. Everything
   committed — the next session must be able to pick up losslessly from the
   repo alone.

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
1. **Read the prior state** (plan + `DESIGN.md` + `PROJECT_STATE.md` +
   `docs/GOTCHAS.md` + the prior plan's commit log + the relevant code). The plan's
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
   are ambiguous and the planning session didn't cover something. The execution
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
   (M-number progress + rulings + post-execution summary). `docs/GOTCHAS.md`
   (new gotchas, grouped by branch, for things that bit during execution).
   The plan file itself: bump the "Status" header from "planned" to "done"
   with the date and a one-line summary; add a final "Execution summary"
   section if the plan grew significantly during execution.
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
| `sweave-web/src/` | React UI (wave-1, R4): pages, components, contexts; build output `dist/` served by the backend |
| `sweave/tools/__init__.py` | DelegateTaskTool (task→worktree→spawn), RouteTaskTool, MemoryTool |
| `sweave/harness/{base,opencode,detect}.py` | AgentSpec/AgentProcess protocol; OpenCode impl |
| `sweave/workspace/manager.py` | git worktrees `.worktrees/{task_id}/{agent}` + PR |
| `sweave/projects.py` + `sweave/api/projects.py` | Project/Session/ChildSession model + endpoints |
| `sweave/config/` | pydantic schemas, ConfigManager (hot reload) |
| `sweave/memory/backends.py` | hindsight embedded/docker/cloud |
| `sweave/runtime/` | delegation store (schema-versioned), JobRunner, SpecialistRuntime (sessions, per-turn prompts), ServeRunner (per-specialist serve), specialist store, escalation store, trace logs |
| `sweave/chat/loop.py` | ChatLoop: chat-turn delegations, transcript composition (M1.7), synthesis, streaming coalescer |
| `sweave/mcp/` | MCP server exposing `defer` + `list_specialists` + `ask_human` to the orchestrator's opencode session |
| `sweave/agents/*/config.yaml` | Omnigent-spec seed agents — LIVE via `sweave/agents/loader.py` (M0); FALLBACK_PROMPTS in tools/ for gaps |

## Gotchas — read before touching an area
Full text lives in **`docs/GOTCHAS.md`**, grouped by branch. When a trigger fires,
read that whole group first.

| If you're about to... | Read group |
|---|---|
| start/stop/restart the server, or config edits aren't applying | Server ops |
| touch `sweave/harness/` or mock the opencode v2 wire | Opencode harness & wire protocol |
| add a test that drives `SpecialistRuntime.run` end-to-end | Writing runtime tests |
| touch the MCP surface (`sweave/mcp/`, `/api/mcp/specialists`, per-project opencode.json) | MCP surface |
| add a field to a Delegation or Session record | Delegation & Session schema |
| wire a new orchestrator surface or add a new engine | Lifecycle & engine contracts |
| construct a path for `agents.yaml` / home-anchored config | Paths & config |
| add a global-side-effect provider in sweave-web (WS, EventSource, long-poll) | sweave-web UI |

## Doc-editing discipline (binds every session)
- **Re-read shared docs from disk immediately before every edit** — never edit from a
  buffer read earlier in the session (two live incidents 2026-09-04: a stale-buffer save
  clobbered both method sections, and the M1.8 plan got re-saved with mojibake). After
  editing, `git status` the file: an unexpected ` M` on a file you didn't touch =
  someone else's write landed. Recovery is always `git show <commit>:<path>` —
  committed blobs are immutable truth; the working tree is negotiable.
- **Never pipe file content through PowerShell redirection** (`>` / `Out-File`) — it
  writes UTF-16 LE with BOM and cp1252 round-trips destroy multi-byte chars (→ U+FFFD).
  Write bytes with `python -c` (binary mode) or let git do it. If a read tool calls a
  text file "binary", probe bytes first (BOM `ff fe` / NUL count / U+FFFD) and check
  whether HEAD's blob differs from the working tree before diagnosing content.

## Running & testing
```bash
python start_server.py 8100 127.0.0.1   # from repo root; logs web.log / web_err.log
python stop_server.py                   # ditto
python run.py --check                   # 13 endpoint tests (SPA mounted from sweave-web/dist)
cd sweave-web && npm test                # vitest unit suite (40+ tests; design + chat + tree)
cd sweave-web && npm run test:e2e        # Playwright e2e (CI gate; needs chromium binary)
```
UI regression pattern: headless Edge via `playwright-core` (`channel: 'msedge'`), assert
`offsetParent` visibility (not just classList), capture console + pageerror. Write the
test before the fix; watch it fail; then fix.

## When you change the design
Update DESIGN.md roadmap/status table in the same change. Update PROJECT_STATE.md's
"Current State" when behavior (not just code) changes. Keep the component status table
honest: ✅ wired / ⚠️ stub / ❌ dead / 📐 planned.
