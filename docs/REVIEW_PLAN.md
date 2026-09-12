# Review deepening — material + assignment + actions

Status: done (2026-09-12). Phase 1 landed (4 commits, gates green —
see Execution summary); Phases 2–3 still sketched. Problem verified live by the user:
a delegation sits in `review` with nothing to review — no diff
(`diff_ref` null, and no diff surface exists anywhere: no endpoint,
no builder), no response text in the modal, no session link, no
assignee, no trigger. The state is a parking lot. This plan makes
it a gate. Parent: `docs/M2_1_FOLLOWUP_PLAN.md` (settle-time
delivery + record header, still unbuilt except the incident round).

## Phase 1 — Review bundle + entry trigger (backend, execution-ready)

### Starting point (executor: verify before touching code)

- `review_request` attaches on the review transition
  (`sweave/runtime/job_runner.py:876-884`); entering `review` also
  sets `needs_attention=True` (follow-up spec A step 1, built
  2026-09-12 in commit 9168050 — cleared on promote, review-aware
  on answer/skip). Only the record-header fold (follow-up spec B)
  is still unbuilt.
- Record header fold (follow-up spec B, unbuilt): detail payload
  has no status/agent/task/output/error. Build it here instead of
  there (this plan subsumes it — note the supersede in the
  follow-up file on landing).
- `engine_session_id` persists since the incident round (v9);
  `output`/`manifest`/`error` already on the record.
- No diff builder exists (verified 2026-09-12: only incidental
  `git diff` uses in transcript snapshotting). `WorktreeManager`
  owns worktree paths + branches (`sweave/workspace/manager.py`).
- Trace `review_requested` event exists for the audit trail.

### Goal state

Entering `review` produces a review bundle AND attention:

1. **Material** (`review_bundle` artifact + pointer on the record):
   unified diff + file list + stats, captured at transition time.
   Worktree delegation → `git diff <base>...<branch>` in that
   worktree. In-tree delegation (no worktree) → `manifest.files_touched`
   diffs vs HEAD when present, else worktree-wide `git status` +
   capped `git diff HEAD` flagged `scope: "unscoped"` (may include
   unrelated user changes — shown, never hidden).
2. **Response + verdict context**: output summary, manifest intent +
   confidence, engine session id — all already stored, folded into
   the detail payload with the record header (status/agent/task/
   output/error/stamps/blocking/attention).
3. **Trigger**: `review` sets `needs_attention=True` (cleared on
   promote) — joins the lane/bugs/inline surfaces. Consumer audit
   first (question-specific branches must not grow answer buttons).
4. **Sizes + secrets**: diff artifact capped (executor picks cap,
   truncation recorded on the pointer); diffs pass through the
   secret tag-and-vault boundary before store/display (R4.4 rule —
   a review must never become a secret-exfil surface).

### Steps

1. Bundle builder + artifact store (`{project}/.sweave/reviews/
   {id}.diff`, pointer `{path, bytes, truncated, scope}` on the
   record — file, not inline: `delegations.json` stays small).
   Captured synchronously on the review transition (the worktree
   may move on; later is never).
2. Record header fold into the detail payload (+ `review_bundle`
   pointer + `engine_session_id` already there).
3. Entry trigger (`needs_attention` set/clear) with the consumer
   audit + pins.
4. Gates: pytest (bundle for worktree + in-tree + manifest-less
   cases, cap/truncation pins, secret-redaction pin, set/clear
   matrix, degradation with missing worktree), `run.py --check`,
   suite 3×, live check (real delegation → review lands with
   diff + lane entry). Docs: DESIGN §4, PROJECT_STATE, GOTCHAS
   (diff caps, unscoped-diff honesty, needs_attention widening).

### Explicit non-goals

- No reviewer auto-assignment (Phase 2).
- No verdict payload (M2.2 owns it).
- No `sweave log` bundle printing beyond a pointer line.
- No R4.4 dependency.

### Risks

- Unscoped in-tree diffs confuse ("is this mine?"): mitigated by
  the explicit `scope` flag + honesty in the UI contract.
- Diffs with secrets: redaction boundary is load-bearing, not
  cosmetic — test with known-shaped secrets.
- Worktree gone at transition (removed post-merge): bundle degrades
  to pointer-without-diff, pinned by test.

## Phase 2 — Reviewer flow (sketched; detailed at its round)

Defer-to-reviewer with the bundle attached; verdict codes
(approve / request-changes + comments); request-changes spawns a
fix-round child of the original delegation carrying the comments;
approve calls the promote endpoint (the automation seam, unchanged).
Default assignee from `reviewer_hint`; human may substitute.
Request-changes loop bounded (executor proposes the bound at
detailing — unbounded review ping-pong is the failure mode).

## Phase 3 — Review pane (R4-thread contracts only)

Diff view + file list, response + manifest + confidence, engine
session link, actions (approve / request changes with comment /
escalate), needs_attention clearance on action. Builds on the
Phase-1 payload — no new endpoints. Implementation user-driven.

## Execution summary (Phase 1, 2026-09-12)

Commits: `da4a1c4` (pre-exec doc fixes) + `5d4df56` (step 1:
bundle + v10) + `2403dfc` (step 2: fold) + `66522d9` (step 3:
trigger). This docs commit closes the phase.

* Step 1 landed `sweave/runtime/review_bundle.py` (redaction
  boundary + scope builder + artifact writer), schema v9→v10,
  and synchronous capture on the review transition
  (`JobRunner._capture_review_bundle` + `review_bundled` /
  `review_bundle_degraded` trace events). 16 tests.
* Step 2 landed the `record` header + `review_bundle` echo in
  `render_detail_view`, endpoint pass-through, and the CLI
  pointer line (subsumes follow-up §B — supersede noted there).
  8 tests; snippet lengths pinned (task 140 = card rule, output
  2000).
* Step 3 hardened the entry trigger: the production store
  flagger shares the single `_review_owes_promotion` rule
  (`make_attention_flagger`, also used by the router answer/skip
  loops), closing the audit-found gap where store-level
  answer/skip/timeout clears wiped the flag on review-owed
  delegations. 6 tests with the real factory wired. R4 consumers
  need no changes (verified: AnswerInline, TurnQuestions,
  ChildEscalationPreview, EscalationSection all gate on pending
  escalation, not the bare flag).
* Amendments (executor-justified): (1) Phase-1 regex redactor
  instead of the R4.4 tag-and-vault (unbuilt; non-goal forbids
  the R4.4 dependency); (2) untracked files appended as marked
  sections (`git diff <base>` never shows them — the truncation
  probe caught it); (3) degraded captures store a pointer
  WITHOUT a file so the surface says why.
* Gates: 799 pytest 3× green (+30: 16 bundle + 8 fold + 6
  trigger), 13/13 `run.py --check` (ephemeral :9091, new code),
  ephemeral-server live probe on :9092 (real v9 review record →
  header present + `review_bundle: null` degrade; live state
  untouched, 180 records before/after). Live :8100 NOT restarted
  (would kill the running turn — owed, user's call); the M2.1
  behavioral check (blocking defer → gated synthesis) stays open
  for the same reason (already live-gated in its own milestone).
* Docs: DESIGN §4 new row + M2 section bullet; PROJECT_STATE
  Threads bullet + Phase-1 entry; GOTCHAS Lifecycle §6 (untracked
  diff) + §7 (store-boundary clears); follow-up §B superseded.
