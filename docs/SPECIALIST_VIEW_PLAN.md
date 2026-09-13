# Specialist live view — full read-only transparency + abort/answer only

Status: planned (2026-09-13). Plan of record for the transparency track.
Companion to `docs/M2_1_FOLLOWUP_PLAN.md` §C (supersede-via-revert) and the
watchdog hardening (`KILL_ON_SILENCE=False`, pre-model bound); does not
replace either. No custom-engine dependency — all steps land on the current
opencode wire.

## Rulings (user-locked 2026-09-13)

1. **Probe first.** A scripted long-tool turn on the installed binary captures
   the full `/event` bus inventory + plugin hook surface + when message-stream
   bytes actually flow. Step 1's sensor choice branches on the probe output,
   never on hope.
2. **Per-tool budget.** Once `tool-started` is known (via whichever sensor the
   probe proves), the turn leaves the 300s byte-clock and runs under a generous
   per-tool budget inside the outer `turn_timeout`. Proposed default 1200s
   (20 min), configurable — locked at execution detailing, not here.
3. **Full read-only live view; abort + answer are the only side-effects.**
   The pane shows everything Sweave knows about a running specialist turn;
   the user may not write, re-prompt, edit files, or open a second input
   funnel through it. Allowed mutations: **abort** the live turn (consented
   engine-stop) and **answer/skip pending permission/question escalations**
   (the existing `…/answer`, `…/skip`, permission reply paths). Follow-ups
   go via the orchestrator — the M1.7 funnel rule stands.
4. **Transcript parity (A-then-maybe-B).** Every agent turn carries its
   transcript to the user — specialist turns no differently from the
   orchestrator's chat. (A) Record-side first: persist what Sweave already
   holds (rendered prompt + preamble + task per turn; tool parts the
   runtime parser currently drops) as trace events, and project them as a
   read-only subchat reusing the Thread primitives. (B) Fetch-side
   engine-truth (`GET /session/{id}/message` rendered as transcript) is
   deferred — it falls out naturally of the custom engine, which owns its
   transcript; if it can be done with opencode it can be done with ours.

## Starting point (verified 2026-09-13 against code, not older bullets)

- Read projector: `sweave/web/detail_view.py:1-44` — trace JSONL → sections
  (`composed_prompt`, `tool_timeline`, `tokens`, `status_timeline`,
  `estimate_vs_actual`, `review_request`, `engine_session_id`, `record`,
  `review_bundle`). Never raises; missing trace/record degrades to nulls.
  Endpoint `GET /api/delegations/{id}/detail` folds record fields through
  (`sweave/web/routers/delegations.py:519-566`).
- Record endpoints on the same router: `GET /api/delegations`,
  `GET /api/delegations/{id}`, `POST …/wait`, `POST …/escalate`,
  `POST …/answer`, `POST …/skip`, `GET …/escalation`,
  `POST …/promote` (`delegations.py:444-890`). **No abort/stop route
  exists** (grep `abort|/stop|terminate|kill` under `sweave/web/routers/`
  hits nothing) — `_attempt_engine_stop` (`specialist_runtime.py:123-201`)
  is runtime-internal and gated by `KILL_ON_SILENCE`.
- Liveness today: stall clock watches **message-stream bytes only**
  (`specialist_runtime.py:963-964` — `wait_for(__anext__, stall)`; parts
  parse only after a chunk lands). Trace already emits `stream_opened`,
  `first_byte`, `stalled{stall_seconds, partial_chars}`, `reasoning`,
  `output_text`, `info_error`, `incomplete_turn`, session/worktree markers
  (grep `trace.append` in `specialist_runtime.py`, 30+ sites).
- Bus subscriber (`sweave/runtime/permission_watch.py:169-195`) keeps **3
  event types** (`permission.asked` / `permission.replied` / `session.idle`)
  and drops everything else uninspected. `session.idle` is completion-only;
  there is no progress consumer on the bus today.
- UI: `TurnDelegations.tsx` (inline cards under chat turns) +
  `pages/children/DetailView.tsx` (shared modal) both read the detail
  payload; both are post-hoc projections — during byte-silence they show
  a frozen last-known state with no elapsed/activity/abort affordance.
- Records carry `engine_session_id` (v9), `worktree_path`, `branch`,
  `status`, `error`, stamps — everything the pane's identity block needs
  is already stored; only *liveness* (last-activity, current tool) and
  *action* (abort) are missing.
- History gap (verified 2026-09-13 via grep): the runtime stream parser
  emits **zero** `tool.*` trace events (text + reasoning only), so the
  detail view's `tool_timeline` (`detail_view.py:207-228`, feeds on
  `tool.started/updated/completed/failed`) is structurally empty for
  specialist turns — only the harness path writes them, which specialists
  don't run on. Likewise a specialist turn's rendered prompt (templated
  system + preamble + task) is composed in memory and never persisted,
  unlike the chat loop's per-turn composed-prompt audit.

## Goal state

A live specialist pane per running delegation — mounted from the existing
delegation cards / Children rows, reading the existing detail payload plus
a new `live` block — showing: identity (agent, task snippet, worktree,
branch, engine session), state (status, elapsed, last-activity + its
source, current tool if known, partial text if any), tool timeline, tokens
so far, pending question/permission with answer affordance, and an Abort
action. Read-only except Abort + Answer/Skip. A byte-silent-but-working
turn reads as "running `pytest` for 8:12, tool started, bounded to 20:00"
— never as a frozen bubble.

A read-only subchat per specialist session — one block per turn (prompt
actually sent, assistant text, tool calls with lifecycle, reasoning,
tokens), projected from the trace like DetailView and rendered with the
same Thread primitives as the orchestrator's chat. Same transparency,
whatever the tier: the orchestrator's turns and the specialists' turns
are both carried to the user, not just the former.

## Steps

0. **Probe: bus + hook inventory during a real long-tool turn (~0.5 sess).**
   Scripted turn (free-tier model per ruling, bounded generation) that runs
   one multi-minute `bash` tool: capture every `/event` bus type seen with
   timestamps, every plugin-hook firing (`permission_bridge_plugin.ts`
   surface — what else fires, with what payload), and message-stream chunk
   timing. Output: event inventory table appended to this plan + the sensor
   decision for step 1 (bus-activity reset vs plugin-ferried `tool-started`
   vs both). Done-gate: inventory committed; a second run reproduces the
   same type set. Keep the script as a drift gate (wire drifts under us).
1. **Activity-based liveness (~0.5–1 sess).** Feed the proven sensor into the
   stall path: bytes OR bus/tool activity resets the clock; `tool-started`
   parks the byte-clock and arms the per-tool budget (proposed 1200s) under
   the outer `turn_timeout`; every sensor reading appends a trace event
   (`tool_started{name, at}`, `activity{source}`) so the pane and `sweave
   log` share one source. Unchanged: header pre-model bound (950s),
   terminal rule (`completed` + `finish`), unknown-id degrade contract.
   Done-gate: pytest (long-tool fixture trips neither bound; genuine wedge
   still trips with truthful message; sensor-absent degrades to today's
   byte-clock — never assumes the probe); suite 3×; `run.py --check`.
2. **Specialist transcript, record-side (~1 sess).** (a) Persist at send
   time: rendered prompt (system render + worktree preamble + task) as a
   per-turn trace event mirroring the chat loop's composed-prompt audit;
   (b) parse + trace tool parts in `_send_message` (same
   pending→running→completed|error shapes the harness emits, so the
   existing `tool_timeline` projector lights up unchanged — unknown part
   types traced raw, never assumed); (c) project per-turn blocks from the
   trace and render the read-only subchat (Thread primitives, no second
   composer). Done-gate: pytest (prompt event present per turn; tool
   fixture yields a populated timeline with lifecycle states; unknown
   parts degrade); `run.py --check`; a real pre-fix delegation still
   projects (empty timeline, not a crash).
3. **Live block + consented abort, backend (~1 sess).** Detail payload gains
   `live` (present iff status == `running`: `elapsed_s`,
   `last_activity_s + source`, `current_tool | null`, `tool_budget_s |
   null`, `abortable: bool`). New `POST /api/delegations/{id}/abort`:
   consented — always attempts engine-stop (NOT gated by
   `KILL_ON_SILENCE`; user-initiated kill ≠ watchdog auto-kill), records
   `abort_sent/acknowledged|UNCONFIRMED` on the trace, sets
   `failed(aborted_by_user)` with no auto-retry; 409 when no live turn
   (busy-guard semantics match revert). Answer/skip/permission-reply paths
   reused verbatim, not duplicated. Done-gate: pytest (abort acknowledged
   → failed+trace; abort rejected → loud UNCONFIRMED, delegation failed
   anyway; abort on settled → 409; `live` nulls when settled); `run.py
   --check`; ephemeral-server live probe (real abort acknowledged).
4. **Pane UI (~1 sess, binds to shipped contracts).** Live row/section on the
   existing cards + DetailView modal reading `live`: identity, elapsed +
   last-activity + source, current tool + budget countdown, partial text,
   pending-question answer inline (existing `TurnQuestions` path), Abort
   with confirm (system-issued, same guard doctrine as skip). WS-pulsed via
   existing `delegation.status_changed` + `specialist.escalated/resolved`
   (add `specialist.activity` only if polling proves insufficient — poll
   first). Frozen-state copy ("no output yet — tool running, bounded")
   replaces the dead bubble. Done-gate: vitest (live block renders,
   settled hides, abort confirms-then-posts), build green, screenshot probe
   of a live turn.
5. **Gates + docs (~0.5 sess).** Full pytest 3×, `run.py --check`, vitest +
   build, live abort scene recorded. Docs: DESIGN §4 rows (live block,
   abort endpoint, activity liveness, specialist transcript), PROJECT_STATE
   entry, GOTCHAS
   (byte-clock vs activity-clock rule; consented-abort vs KILL_ON_SILENCE
   split; bus types consumed — update when the wire drifts).

## Explicit non-goals

- No interactive shell / terminal into the specialist serve; no file editing
  from the pane; no re-prompt or second composer (orchestrator stays the
  only input funnel).
- No mid-tool output streaming unless the probe proves the wire emits it
  (start/end + budget + abort is the guaranteed bar).
- No promote/merge from the pane (Children promotion path unchanged).
- No sqlite reads (wire-only ruling stands); no new MCP tool (abort/answer
  are HTTP routes, matching the existing escalation surface).
- No specialist-session undo here (that's §C supersede-via-revert).
- No fetch-side engine-truth transcript on the opencode wire (ruling 4B —
  deferred to the custom engine, which owns its transcript).

## Risks

- Probe finds the bus as silent as the stream mid-tool → step 1 becomes
  plugin-ferried `tool-started` only; mid-tool wedges stay indistinguishable
  from mid-tool work until the tool budget trips. Accepted: bounded +
  visible + abortable beats silent + dead.
- Opencode upgrade changes bus types or hook surface → the probe script is
  the drift gate; `sweave doctor` rate alarms (CUSTOM_ENGINE plan) catch it
  within one turn.
- `specialist.harness` schema work (custom engine step 4) touches the same
  record — coordinate, additive fields only, migration per gotcha #12 rule.
- Tool-part shapes drift per opencode version → the step-2 parser matches
  defensively (type-string switch, unknown traced raw); a version bump that
  renames parts degrades to "unknown part" rows, never a turn failure.
