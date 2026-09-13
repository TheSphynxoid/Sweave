# M2.1 follow-up — settle-time delivery + detail record fold

Status: planned (2026-09-12). Covers two diagnostics from session
`Sweave-20260911-213619-096e65` (halt-after-review + thin DetailView).
Backend items are M-thread execution-ready below; UI items are marked
R4-thread and stop at component-level contracts (no styling).

## A. Settle-time delivery + review-entry trigger

Problem: a non-blocking child settling after its chat turn closed is
silent. The turn ended correctly (blocking=false), but nothing catches
the result: no WS nudge, no thread note, `needs_attention` stays false,
and `review` (a state awaiting human promotion) is the quietest state
in the system. The orchestrator additionally promises follow-ups
("I'll report SHAs when back") the machinery cannot keep.

### Locked semantics (from 2026-09-12 diagnosis review)

1. Turn-close while children run stays legal. Coherence comes from
   guaranteed settle-time delivery, not from holding turns open
   (the per-session lock makes long holds user-hostile).
2. Entering `review` sets `needs_attention=True` (cleared on promote).
   Rationale: the flag means "human action needed" — today that is
   answer; after this it is answer OR promote. Review arrival joins
   the existing attention surfaces (escalation lane, bugs lane,
   inline cards) instead of inventing a parallel channel.
3. Reviewer children always notify with the verdict surfaced
   (review-result instrumentation) regardless of `blocking` — verdicts
   are decision-grade. General case of (2), not an exception.
4. Orchestrator prompt: no follow-up promises for fire-and-forget
   defers. If the turn can't deliver it, the turn doesn't offer it.

### Starting point (executor: verify before touching code)

- `needs_attention` producers/consumers: audit every read of the
  flag and every branch on escalation `kind`/`status` — some assume
  "pending question" (e.g. inline answer cards, `EscalationSection`
  404-hide). Widening the flag must not put answer buttons on
  promotions.
- `TurnDelegations` subscriptions (`status_changed`, `escalated`,
  `escalation_resolved`): confirm a late-settling child re-renders
  the card under the closed turn when online.
- ChatLoop synthesis text for empty join sets ("nothing to
  synthesize" reads as a stall — reword to a handoff note naming the
  running children).
- Orchestrator prompt's defer contract (`agents/orchestrator/`
  + managed `sweave-orchestrator` prompt): add the no-follow-up rule.

### Steps

1. Review-entry attention: `review` transition sets
   `needs_attention=True` (+ trace event with source), `promote`
   clears it. Consumer audit first (step 0): list every
   `needs_attention`/kind/status branch, adjust question-specific
   ones, pin with tests.
2. Late-settle surfacing:-grey/green "finished after turn closed"
   state on `TurnDelegations` rows + the review hint inline
   (reviewer_hint + one-click "defer to reviewer" = the locked
   explicit resolution path, currently undiscoverable).
3. Synthesis handoff note + prompt rule (no promised follow-ups).
4. Gates: pytest (flag set/cleared matrix incl. promote path,
   consumer-adjustment pins, late-settle WS unit), `run.py --check`,
   suite 3×, live check (fire-and-forget → review lands → lane +
   card update with NO turn reopen).

### Explicit non-goals

- No turn reopening, no per-arrival orchestrator turns.
- No state-machine guard requiring review material (filed for R2:
  `review` unenterable without diff/output — needs the material
  contract first).
- No R4.4 dependency.

## B. Detail record fold (backend) + modal sections (R4-thread)

> SUPERSEDED (backend half, 2026-09-12): built by REVIEW Phase 1
> (`docs/REVIEW_PLAN.md` — record header + bundle pointer in the
> detail payload, endpoint pass-through, CLI pointer line). The R4
> modal contracts below still stand as written; implementation
> remains user-driven.

Problem: `GET .../detail` returns a pure trace projection — no record
header — so the modal cannot show status/agent/task/output/error, and
renders only 5 of the 7 shipped sections (`estimate_vs_actual` and
`review_request` fetched and discarded). For specialist task turns the
trace sections are structurally empty (chat-turn audit artifacts), so
"full details" shows less than the inline card. (Side note, not a bug:
`review_request: null` seen live 2026-09-12 is the stale pre-M2.1
server; HEAD's router passes it through. Dies on restart.)

### Backend (M-thread, execution-ready)

1. `render_detail_view` gains a `record` header: status, agent, task
   (+truncated task snippet rule — executor picks length, pins it),
   output summary, error, created/completed stamps, blocking,
   needs_attention. Never raises; missing record degrades like
   missing trace.
2. Endpoint passes the record through (same fold as estimate/review).
3. CLI `sweave log` prints the header (it already prints the
   projection — same pattern).
4. Gates: pytest (header present, degradation pins, snippet-length
   pin), `run.py --check`, suite 3×.

### UI (R4-thread — contracts only, implementation user-driven)

- Render Output summary, Review (hint + diff refs + promote /
  defer-to-reviewer actions), and Estimate sections from the payload
  fields above (all already/soon shipped — no new endpoints).
- Empty states that explain instead of "No data" (e.g. task turns
  don't emit composed prompts; point at Output).
- Late-settle row state + review hint inline per section A.

## Gates for the unit

Backend steps ship first with API contracts + pytest (R4-parallel
discipline); UI binds when driven. PROJECT_STATE + DESIGN §4 rows for
the backend half; GOTCHAS for the needs_attention widening.

## Executed (2026-09-12) — incident round (no restart)

Live incident Sweave-20260911-213619-096e65 drove three backend
items ahead of the spec above (spec sections A/B still stand as
written; UI half untouched):

* Template-send stall bound (`specialist_runtime._bounded_system_send`):
  the 16m40s unwatched harness send now trips at 300s traced as
  phase=system_prompt, and a failed send fails loudly instead of
  running anonymous. Forensics: opencode logged no `asking` event in
  the window (permission-hang disfavored); the serve answered session
  setup at 03:27:04 then produced zero bytes (serve/model-side wedge
  class, same as 2026-09-10). Also noted: serve was 1.18.30, not the
  pinned 1.18.29 (drift watch, not chased).
* Stall errors carry turn age (`t0` param; legacy strings preserved
  without it) — the "300s" message for a 21-minute hang.
* Per-delegation `engine_session_id` (schema v9 + migration +
  run-time set + JobRunner persist + detail fold + CLI skipped
  deliberately: `sweave log` stays trace-only per the M2.0
  precedent; HTTP endpoint carries it).
* Orchestrator prompt: `blocking`/`estimate` documented + follow-up
  rules (no promises for fire-and-forget; previous turns' children
  checked, not assumed).
* Correction to the session's own [23]: `blocking: true` DID hold
  the turn (22 min); the child transport failed underneath it, and
  the "reviewer stalled" claim was a cross-turn scoping misread
  (reviewer belonged to the previous turn, completed 03:23 WITH
  output). The prompt's cross-turn rule covers both misreads.
* Gates: 8 new tests (`test_m2_1_followup_hardening.py`) + schema-pin
  updates across M2.0/M2.1 suites; 754 pytest 3× green. `run.py
  --check` skipped deliberately (second sweeper vs live server,
  GOTCHAS M1.13). Live pickup needs a server restart (not done —
  user's call).

## C. Supersede-via-revert (undo/redo semantics, 2026-09-13; spec)

User-locked semantics: **undo = revert the session to the state
before the target turn** (later messages superseded, engine history
truncated, file effects rolled back); **redo/retry = revert and
replay the same message** (revert + re-send the original content).
This retires "pure retry keeps the binding" — today's diagnosis
showed stale-session reuse inheriting a wedged generation, so
engine-rewind-on-every-rerun is a feature.

### Probe results (installed 1.18.30, live, scripted)

`scripts/probe_revert_1_18.py` + `scripts/probe_revert_files_1_18.py`
(keep as the drift gate):

- Routes LIVE: `POST /session/{id}/revert {messageID}`,
  `POST /session/{id}/unrevert` (both 200), plus a v1-style
  `/api/session/{id}/revert/{stage,commit,clear}` family (unused).
- Pointer semantics: revert sets `session.revert = {messageID,
  snapshot, diff}`; the message LISTING still returns everything
  (truncation is view-level) — the NEXT prompt after a revert
  replaces the reverted tail. Our undo = revert + replacement send /
  redo = revert + same-message replay; the pointer does the work.
- FILE-STATE restore confirmed: with `git init` in the project dir,
  revert DELETED the turn's file (pointer.diff showed `deleted file
  mode`), unrevert restored it byte-identical. Snapshot is a shadow
  git repo (separate GIT_DIR, GIT_WORK_TREE = project) — rebuilt as
  tree hashes, scoped to files the session's patches name.
- HARD REQUIREMENT: snapshots (and file restore) enable ONLY in git
  repos (`snapshot.enabled: state.vcs !== "git" → false`). Sweave
  projects are always git repos (incl. in-tree chat) — still, the
  capability probe must verify vcs before promising file-undo.
- Busy guard: engine refuses revert mid-turn (`assertNotBusy`) —
  matches our TurnActiveError; an undo while a turn runs is a 409 on
  both sides.
- Model calls in probes: free tier only (muse-spark free), per user
  ruling; generation cost bounded (~3 KB streams).

### Design

1. **Capture engine message ids** per chat message (the pattern of
   engine_session_id): the v2 stream emits message ids (`msg_*`);
   chat message `metadata` gains `engine_message_id` (write at
   persist). Prerequisite for targeted revert.
2. **Undo (revert)**: UI/API action on a turn → SupersedeService:
   `revert(session, target_msg.engine_message_id)` (opencode), then
   flag later messages superseded + abort live children of the
   reverted turns (abort already built). Rotation becomes the
   FALLBACK (wire-drift: capability-probe revert at serve spawn;
   absent → old rotate behavior, never assumed).
3. **Redo/retry**: revert + replay same content through the normal
   turn body. One path for both (revert precedes replay).
4. **Custom engine ownership ruling (user-asked)**: file-state undo
   is ENGINE-level (per-turn file tracking must exist at execution
   time), mirrored on the opencode shadow-git design (separate
   GIT_DIR + write-tree/checkout scoped to session patches). Sweave
   keeps records, flags, and orchestration; it never replays file
   patches itself. For sweave-engine: turn-scoped journal or
   shadow-tree, decided at its Step 2 build; the revert verb is part
   of the engine contract either way (`revert(to_message)` on
   AgentProcess).
5. **Fake-model probes**: per user ruling — probes use free models
   or structured fakes; never paid tiers.

### Non-goals

- No /stage|commit|clear usage (v1 family; revisit if pointer
  semantics prove limiting).
- No partial (partID) revert in v1 (whole-message granularity).
- No Sweave-side patch replay (engine owns file state).
- Undo of SPECIALIST sessions is in-tree-risky (a specialist may
  have legitimate user-visible work): v1 scope = orchestrator chat
  sessions only; specialist undo needs its own ruling.
