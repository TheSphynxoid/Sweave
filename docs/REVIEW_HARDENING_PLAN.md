# Reviewer + Engine Hardening — plan of record

Status: **done** (2026-09-15 — steps 1–4 landed as step commits
`c0aa557`/`3abd2ab`/`644232c`/`dad6367`; step 5 stays sequenced).
Owner: this session.
Predecessor reading: DESIGN.md §6 (M2.2/M2.3/M2.4, Review Phase 1/2),
`docs/REVIEW_PLAN.md`, `docs/CUSTOM_ENGINE_PLAN.md`, PROJECT_STATE.md.

## 0. User rulings locked 2026-09-15 (all from one chat round)

1. **sweave-engine is the spec; opencode is the parity baseline.** New
   integration capability lands engine-first and is never cut down to
   what opencode's bus exposes. Opencode stays green (contract tests,
   live gates) but never gates engine progress. (Prior art: 2026-09-14
   fallback removal — fail loud across harnesses.)
2. **Reviewer is the load-bearing gate; it gets perfection-grade
   investment before breadth.** Ordering: verdict payload (M2.2 shape)
   first — assignment (Phase 2), calibration (M2.4), cross-review hang
   off the verdict shape. Reviewer tooling amplifies judgment; the
   verdict captures it.
3. **Reviewer is not a hardcoded exception.** It is the first carrier
   of a generic "review another delegation's work" permission (tree
   access + tool doctrine + verdict). A user-added security specialist
   gets the same shape with zero special-casing.
4. **`inherit` fallback to project root is spec, not accident.** A
   reviewer deferred by the orchestrator (treeless chat parent) lands
   on the orchestrator's POV = project root — by design, and the
   prompt must say so (not "wasn't the plan"). Minting the
   orchestrator its own tree would be waste.
5. **Fire-and-forget over plan-favoring.** The 30-min total cap makes
   long plan execution unusable (live exhibit: killed the parallel
   worker 2026-09-15). "Lift" = replace time-gating with
   progress-gating (heartbeats/liveness + consented abort), keeping a
   very high runaway fuse. Sequenced AFTER (a) native specialist
   execution access (view track) and (b) correct streaming — both are
   load-bearing for supervision, and both are engine-first per ruling 1
   (opencode gets ferries where cheap, documented gaps where not).
6. **Reviewer doctrine: "prove, don't fix."** Read-mostly by policy
   (M2.3 locked-reviewer direction); write/edit retained to prove
   findings (repro tests, suggestion patches), fixes return as
   blocking findings. Commit authority stays with the implementer.
7. **Scratch discipline.** Throwaway runner output/scripts go to the
   OS temp dir (never repo root); the reviewer audits stray
   `test_out*`/ad-hoc scripts as a non-blocking finding. A
   full-capture test affordance (output → trace/transcript, queryable)
   is the real fix; file habit is a workaround for truncation.

## 1. Starting point (verified 2026-09-15 against code)

* Seeds (`sweave/agents/{backend,frontend,reviewer}/config.yaml`):
  Workspace block uses single-brace `.worktrees/{task_id}-<role>` —
  never renders (engine needs `{{var}}`), hardcodes a suffix the
  runtime doesn't use (`{task_id}-{agent}`), and is relative. Static
  prompt ⇒ one-off send at session create, stale across reused
  sessions. `os_env.cwd` is loaded (`loader.py:71`) but has zero
  consumers. Real grounding is the per-message preamble
  (`specialist_runtime.py:1210`) + serve `cwd` — which contradicts the
  prompt, so models probe with `pwd`/`cd`.
* Engine truncation (`sweave-engine/src/tools.js`): `read` with no
  `limit` returns the whole file (`lines.slice(start)`), then a flat
  32K **head** cut. Bash head-cut too — drops test failures at the
  end. Marker names the cut but teaches no recovery. Opencode parity:
  read paginated by default (limit 2000) + byte cap + `Use offset=`
  teaching; bash tail-cut. Live sprawl in repo root (`test_out*.txt`,
  `test_full.txt`, `theme_fail.txt` — vitest outputs, one written
  from inside a worktree) is the learned workaround.
* Worktree settle (`job_runner.py:_remove_task_worktree`): non-force
  `remove` ⇒ any dirty tree at settle survives (live leak: `a5884977`
  `failed` + owned, tree kept; 40-deep `review` backlog keeps trees by
  design; 11 `sweave/*` branches vs 6 trees = keep-branch half).
  `.gitignore` covers `runfull.py`/`full_out*`/`scratch-*` (10aac08)
  but not the `test_out*`/`test_full`/`theme_fail` shapes.
* Untouched by this plan (other live threads): `sweave-web/src/pages/Stats.tsx`
  (+ tests) dirty in working tree — hands off. M2.2 verdict payload,
  Phase-2 assignment, M2.4 golden set, progress supervisor, gotcha
  system, gated transcript reads: specified here only as sequencing.

## 2. Steps

### Step 1 — Policy-aware seed workspace (this session)
* `prompt_template.py`: add `worktree_policy` + `workspace` (one
  pre-built sentence derived from `(policy, worktree_owned,
  parent_task_id, kind)`):
  isolated+owned → isolated tree, commit freely; inherit+shared →
  shared tree, coordinate, never retire; none/fallback/chat →
  project root, live tree, be careful; inherit-from-chat names the
  spec ("orchestrator's view, by design" — ruling 4).
* All 3 seeds: Workspace block → `{{workspace}}` + absolute
  `{{worktree_path}}` line + scratch-discipline line (ruling 7).
  Fixes single-brace bug, suffix bug, staleness (opts into per-turn
  re-render), and the `pwd`-echo.
* Tests: extend `tests/test_prompt_template.py` (4 modes + fallback
  naming + unknown-policy degrade). Seed-text pins (no hardcoded
  `.worktrees/` path survives).
* Done-gate: new tests green; full pytest green; seeds load
  (`test_agents_loader.py`).

### Step 2 — Engine truncation parity (this session, engine-first per ruling 1)
* `tools.js`: `read` defaults to 2000 lines when `limit` omitted
  (opencode parity; explicit `limit` still wins); oversized reads
  teach recovery (`Showing lines X–Y of Z. Use offset=N to
  continue.`); `bash` truncation becomes **tail**-cut (failures at
  the end survive) with the same honest marker; tool descriptions
  document the contract.
* Tests: hermetic sidecar tests (existing `test_engine_tools.py`
  pattern — node present, v24): default-paged read, explicit-limit
  win, tail-cut bash, teaching marker text, small-result
  byte-identical passthrough. Update any existing marker asserts.
* Done-gate: new + existing engine tests green; no loop-behavior
  change (cap values only + message text).

### Step 3 — Scratch containment (this session)
* `.gitignore`: `test_out*.txt`, `test_full*.txt`, `theme_fail*.txt`
  (shapes observed live); remove the 9 junk files from repo root.
* Reviewer doctrine line lands via Step 1 seed edit (audit stray
  scratch as non-blocking finding). GOTCHAS entry for the scratch
  rule (re-read file before edit per discipline).
* Done-gate: `git status --short` clean of scratch; pytest untouched.

### Step 4 — Settle hardening (this session, minimal)
* `_remove_task_worktree`: before remove, best-effort
  `git add -A + git commit "sweave: settle WIP …"` (trace
  `worktree_wip_committed`); then non-force remove. Work preserved
  on the kept branch; dirty no longer blocks retirement. Never
  force-removes uncommitted work. `review` still keeps its tree;
  non-owned still skipped.
* `WorktreeManager.prune()` passthrough (`git worktree prune`);
  wired into `POST /api/worktrees/clean` + CLI `--clean`.
* Tests: extend `test_task_worktrees.py`/`test_worktree_policy.py`
  (dirty tree at settle → committed + removed + branch kept;
  review keeps; inherit not-owned never touched).
* Done-gate: settle tests green; live `a5884977`-class leak gone.

### Step 5 — Specified, NOT built (sequencing for later rounds)
* **M2.2 verdict payload** (first, per ruling 2): structured
  approve/request-changes + confidence + `gotcha_hits` +
  `output_claims_checked` fields reserved now for the gotcha/gated
  systems below.
* **Review-target resolution**: `inherit` grows from direct-parent
  to review-target (`review_request` → implementation delegation);
  generic permission any agent can carry (ruling 3).
* **Reviewer kit**: scoped suite runs, repro-test proof, per-file
  verdicts with lineage, suggestion patches, `tsc`/`ruff`/secret/
  dep-audit gates, Playwright visual for frontend, trace forensics;
  future skills (`review-checklist/<stack>`, `test-planner`,
  `diff-triage`, `security-audit`) — no new MCP slots.
* **Gated turn-result reads**: pull-not-push (header/summary auto,
  full output + transcript + tool timeline behind explicit read;
  independent diff pass first, claims check second). Rationale:
  chain-budget + judgment hygiene.
* **Gotchas as a system**: structured records, dispatch-time
  injection by area, reviewer audits compliance + proposes new ones
  (human-approved); hindsight = soft patterns, gotchas = hard rules.
* **Progress supervisor** (ruling 5): specialist-view (a) +
  engine-first streaming (b) → heartbeats replace the clock; 30-min
  becomes a multi-hour runaway fuse. Opencode activity ferry where
  cheap (bridge-plugin pattern), documented gaps where not.
* **Full-capture test affordance** (ruling 7): run-tests path with
  output into trace/transcript (queryable, paged) — kills the file
  habit's cause. Batch-tool limits explicitly rejected (punishes the
  workaround, not the cause).

## 3. Non-goals (this session)

* No M2.2/Phase-2/M2.4 schema or endpoints; no verdict payload.
* No supervisor/timer changes (30-min stays until the view +
  streaming tracks land).
* No gotcha store, no transcript-search endpoint, no test-runner tool.
* No opencode-side changes (parity baseline stays as-is).
* No UI changes; no touching the dirty Stats thread files.

## 4. Risks

* Seed text edits change every specialist's system prompt — mitigated
  by per-turn render tests + loader checks, and the sentences are
  small/factual.
* Engine message-text changes could break marker asserts elsewhere —
  grep `truncated` across tests before finalizing.
* Settle-commit adds `git` writes on a hot path — best-effort +
  never-raises + trace-recorded; a commit failure degrades to today's
  behavior.
* Parallel worker owns worktrees/branches under `sweave/*` — we never
  prune branches, only our own settle path + `prune` of dead
  registrations; no `git worktree prune --expire` games.
