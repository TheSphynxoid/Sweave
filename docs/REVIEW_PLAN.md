# Review deepening — material + assignment + actions

Status: planned (2026-09-12). Problem verified live by the user:
a delegation sits in `review` with nothing to review — no diff
(`diff_ref` null, and no diff surface exists anywhere: no endpoint,
no builder), no response text in the modal, no session link, no
assignee, no trigger. The state is a parking lot. This plan makes
it a gate. Parent: `docs/M2_1_FOLLOWUP_PLAN.md` (settle-time
delivery + record header, still unbuilt except the incident round).

## Phase 1 — Review bundle + entry trigger (backend, execution-ready)

### Starting point (executor: verify before touching code)

- `review_request` attaches on the review transition
  (`job_runner.py:872-884`); `needs_attention` is NOT set there
  (follow-up spec A, unbuilt).
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
