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
