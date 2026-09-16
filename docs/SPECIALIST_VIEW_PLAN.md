# Specialist live view — full read-only transparency + abort/answer only

Status: planned (2026-09-13); amended 2026-09-16 (Steps 2–4 rescoped:
engine-first transcript + tabbed detail + child live-forwarding; see
Amendment 2026-09-16 below). Plan of record for the transparency track.
Companion to `docs/M2_1_FOLLOWUP_PLAN.md` §C (supersede-via-revert) and the
watchdog hardening (`KILL_ON_SILENCE=True` since the no-rotation ruling —
was `False` at plan time; a declared stall now kills, and sessions are
never rotated); does not replace either. Engine-first since the 2026-09-15
ruling (DESIGN §4: new integration capability lands engine-first, never
cut down to what opencode's bus exposes); opencode keeps ferries where
cheap, documented gaps where not.

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
   DONE 2026-09-13 (`scripts/probe_bus_inventory_1_18.py`, 3 free-tier
   runs on 1.18.30, same 13-type set twice — drift gate holds).

   Inventory (150s bash sleep, `timeout` passed explicitly in ms):
   `server.connected` 1, `server.heartbeat` ~16 (every 10s, keepalive
   only — never progress), `session.status` 6 (busy at boundaries),
   `session.idle` 1 (terminal), `message.part.delta` ~106 (model text
   only), `message.part.updated` 15, `message.updated` 10,
   `session.diff` 3 (empty), `session.updated` 5, `catalog.updated` 2,
   `integration.updated` 1, `reference.updated` 1, `plugin.added` 45
   (serve boot, not the turn). Message stream: 1 chunk (single-shot
   delivery confirmed — brace-depth parse, not line-split). Tool part
   keys: `callID,id,messageID,metadata,sessionID,state,tool,type`;
   state keys: `input,metadata,output,status,time,title`; bash accepts
   `{"command", "timeout"}` with timeout in ms (model passed
   170000–180000; kill message otherwise reads "terminated command
   after exceeding timeout 160 ms" — run 1's lesson, prompt now
   instructs the timeout explicitly).

   SENSOR DECISION (locked): plugin-ferried `tool-started` ONLY.
   Progress-bucket histogram (10s, heartbeats excluded) shows 100
   events at setup, ZERO across the entire 10–140s tool window, then
   completion at 150–160s: the bus is as silent as the stream
   mid-tool (the run-1 10k-delta storm was model verbosity at the
   boundaries, not tool progress). So: no bus-activity reset — the
   bridge plugin ferries `tool-started` in-process (it sees the call;
   same ferry pattern as the permission bridge), which parks the
   byte-clock and arms the per-tool budget; mid-tool wedges stay
   indistinguishable from work until the budget trips (accepted:
   bounded + visible + abortable beats silent + dead). This also
   unblocks custom-engine step 2 (same sensor). Sensor-absent still
   degrades to today's byte-clock, never assumes the probe.
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
- No general docking system here (2026-09-16 ruling: a dockable-pane
  framework — stats docked one side, chat the other, etc. — is desirable
  but complex and gets its own plan; this track builds the tab content
  container-agnostic so it can graduate into a dock later, but ships no
  dock).

## Amendment 2026-09-16 — engine-first transcript + tabbed detail + child
## live-forwarding (user-locked; Steps 2–4 rescoped, Steps 0–1 + 5 stand)

Motivation: the user's three-part report — (1) the chat Specialist menu
does not update in realtime, (2) same inside the Children tab, (3) the
tool history timeline should read newest-first. Investigation (planning
rounds 2026-09-16, verified against code not docs) found the freeze is
the plan's own Starting-point prediction: `TurnDelegations.tsx` refetches
only on `delegation.status_changed` + escalation events (transitions move
pills; mid-turn nothing ticks), Children invalidates on the same 3
events, and the shared `DetailView` modal has zero subscriptions (a
one-shot snapshot). No signal exists mid-turn because `JobRunner._run`
calls `specialist_runtime.run(...)` without the `on_chunk` / `on_reasoning`
/ `on_tool` callbacks both harnesses already honor (`job_runner.py:1629+`
vs `specialist_runtime.py:526-532`; only the chat loop passes them).

Rulings locked 2026-09-16 (planning discussion):

1. **Q1 Yes — engine-first with opencode-degrade.** Content is not the
   blocker on the engine path: the sidecar journal (`sweave-engine/src/
   sessions.js`) already holds prompt + text + tools + reasoning per turn
   and the wire emits the full frozen vocab (`token`, `reasoning`,
   `tool.*`, `step.boundary`, `tokens_used`, `done{user_message_id}` —
   `sweave/engine/protocol.py:TRACE_EVENT_NAMES`). What is missing is a
   read path (no endpoint projects the journal into per-turn blocks) plus
   live-forwarding. Opencode turns degrade to today's sections until
   their capture hardens: the rendered prompt is composed in memory
   (preamble + message, templated system — `specialist_runtime.py:1217+`)
   but only `prompt_sent{prompt: delegation.task}` is traced
   (`job_runner.py:1410`); the fix is a trace event at the send sites
   capturing what was actually sent (mirroring the chat `composed_prompt`
   audit), not new availability. The stale "zero `tool.*`" Starting-point
   bullet is corrected: `_send_message` now parses tool parts via
   `_emit_opencode_tool` + forwards `on_tool`
   (`specialist_runtime.py:1785-1826`); detailing re-verifies lifecycle
   completeness by grep, not by docstring.
2. **Q2 — tool-timeline ordering: newest-first confirmed for the
   timeline surface; status timeline stays chronological.** The reversal
   ask targets the audit list (recent work on top), not the conversation.
   Transcript blocks stay chronological (conversation order); `status_changed`
   stays chronological (causal chain); CLI/backend/projector order is
   untouched (UI-only `.reverse()` at render in `ToolTimelineSection`).
   Kept for the Children tab surface (the shared `DetailView` modal),
   which is where the request originated.
3. **Q3 — live mechanism: child callback-forwarding + poll fallback.**
   `JobRunner._run` forwards the three callbacks for child turns; child-id
   keying on additive events only (whether new names or same names with
   the child id in the payload is locked at execution detailing — it is a
   WS-vocab decision); 3–5s poll fallback while `running/queued`; no other
   new WS vocab. Same standing answer as Q2's mechanism half: poll-first
   per Step 4 ("new WS vocab only if polling proves insufficient").
4. **Q4 — compact cards kept, expanded tail preview + full-details
   affordance.** Cards stay as compact headers (pill + snippet + running
   count — cheap situational awareness the transcript does not replace);
   expansion shows the tail of the specialist's execution; a full-details
   affordance opens the tabbed surface (UX-2). No card deletion.
5. **Q5 — plan home: amend this file (Steps 2+4 rescoped below).** No
   third plan; this slice IS Steps 2+4.
6. **Q6 — UX-5 dock deferred with its own future plan; tab content
   built container-agnostic.** A general docking system (stats one side,
   chat the other, specialist transcript docked beside the thread, …)
   is desirable but complex — deferred as a target with its own plan,
   not designed here. Consequence binding this slice: tab components
   must not assume the modal (no modal-only hooks, no `document.body`
   portal baked into content components) so they can graduate into a
   dock later. The dock itself shows one specialist at a time with a
   selector (N parallel panes would re-create the alt-tabbing the
   funnels exist to eliminate).

### Rescoped Step 2 — engine-first transcript read path + opencode capture

2a. **Engine journal → per-turn blocks (read path, additive).**
Project the sidecar journal into per-turn blocks (prompt actually sent,
assistant text, tool calls with lifecycle, reasoning, tokens) through
the existing detail fold as an additive key (name locked at execution
detailing, e.g. `transcript`). Transparency track owns the payload
(engine plan §7: engine turns render through the same projector; engine
work needing a new field extends the payload, never a second surface).
Pre-`/run`-turns (multi-iteration loop turns) project as one block per
`/run` turn; `user_message_id` (protocol v3) names the prompt unit.
Done-gate: pytest (journal fixture → blocks with prompt/text/tools/
reasoning/tokens; unknown shapes degrade, never raise); a real
pre-change delegation projects (no `transcript` key content, not a
crash); `run.py --check`.
2b. **Opencode actually-sent prompt capture (trace, additive).** At the
opencode send sites (`_run_opencode` body construction +
`_bounded_system_send` templated render), trace what the wire actually
carried — full text vs section sizes mirrors the chat `composed_prompt`
audit (lock at execution detailing; sizes bound trace growth on long
turns). Never fails the turn. Done-gate: pytest (rendered-preamble turn
yields the event with the wire text/sizes; static-prompt turns keep the
legacy one-off path untouched); suite 3×.
2c. **Tool-lifecycle verification (grep, not docstring).** Re-verify
`_emit_opencode_tool` lifecycle completeness (pending → running →
completed | error on every part shape the installed binary emits;
unknown part types traced raw, never assumed — the standing risk
below). Done-gate: fixture over recorded part shapes yields a populated
timeline with lifecycle states; version-bump drift degrades to
"unknown part" rows, never a turn failure.

### Rescoped Step 4 — tabbed detail + cards + live (binds to shipped contracts)

4a. **Tabbed `DetailView` (UX-2): Overview | Transcript | Tools |
Prompt | Tokens/Status.** Same component the chat cards and Children
rows already open; tabs read the detail fold (Transcript = rescoped-2a
blocks, chronological; Tools = `tool_timeline` newest-first per ruling
Q2, UI-only reverse; Prompt = composed/audit sections; Tokens/Status =
existing sections, chronological). Opencode turns show today's sections
with the Transcript tab degrading to the available content (never an
empty promise). Components container-agnostic per ruling Q6 (content vs
modal chrome separated; no portal baked into tab components).
Done-gate: vitest (tabs render per harness fixture; settled hides live
affordances; Tools order pinned newest-first; Transcript chronological),
`npm run build` green, screenshot probe of a live turn.
4b. **Cards: tail preview + full-details affordance (ruling Q4).**
Expanded card shows the tail of the execution (bounded snippet, latest
activity first); "Open full detail" opens the tabbed surface at the
Transcript tab. Header (pill + snippet + running count) unchanged.
4c. **Live: child callback-forwarding + poll fallback (ruling Q3).**
`JobRunner._run` passes `on_chunk` / `on_reasoning` / `on_tool` for
child turns (coalesced text event or trace-tail poll per execution
detailing; per-callID row events; child-id keying, additive events
only); UI refetches detail + invalidates `["delegations"]` on the same
cadence (3–5s) while any shown record is `running/queued`, idle/settled
= no polling; existing 3-event WS invalidation kept; open modal
subscribes (it never had one — the freeze's third mechanism). Frozen-state
copy ("no output yet — tool running, bounded") replaces the dead bubble.
Done-gate: vitest (live block renders while running, hides on settle,
poll stops on settle), ephemeral-server live probe (real child turn
ticks the open modal + card without reload), `run.py --check`.

### Standing (unchanged)

Steps 0–1 (probe DONE, activity liveness), 3 (live block + consented
abort), 5 (gates + docs) stand as written. Rescoped 2/4 feed Step 3's
`live` block (elapsed/activity/current-tool/budget/partial text) rather
than replacing it; abort/answer paths untouched. The general dock (Q6)
is explicitly out of this track — its own future plan. The Q2/Q3
rulings above settle this round's open questions: the tool-timeline
surface reads newest-first (Transcript/CLI/backend order untouched),
and child turns get callback-forwarding + 3–5s poll fallback with no
other new WS vocab — execution detailing locks only the additive key
name, the full-text-vs-sizes capture shape, the WS keying choice (new
names vs same names + child id), and the coalesced-text-vs-trace-tail
forwarding shape.

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

## Amendment execution record (backend slice, 2026-09-16)

Steps 2a/2b/2c/4c-backend executed in four step commits (frontend
4a/4b/4c-frontend landed from the parallel thread in the same
window):

- **2a `624ef4c`** — engine journal read path (`sweave/web/transcript_view.py`) + the ADDITIVE detail-fold key LOCKED as `transcript` (`detail_view.py:325-345`, `journal_path` param added for offline readers). One block per `/run` turn keyed by `user_message_id` (protocol v3); tools joined via assistant `toolCalls` + journal `role:tool` results by `toolCallId`; reasoning trace-joined via `engine_user_message` turn boundaries (the journal does not persist reasoning); tokens from per-turn `tokens_used`. Caps 20K/8K/200 with honest markers. Degrade contract tested: pre-change records + opencode turns project contentless (None), never crash. Gates: 12 tests (`tests/test_transcript_view.py`); recorded production journal session projected (4 blocks incl. failed + tool-carrying turns).
- **2b `c70b459`** — `_run_opencode` body construction + `_bounded_system_send` templated render trace `wire_prompt` (`specialist_runtime.py:~1252` system_render / `~1296` turn). Capture shape LOCKED: sizes + 200-char preview (mirrors the chat `composed_prompt` audit; sizes bound trace growth; the full task text already rides `prompt_sent` — exactly one full copy per turn). Best-effort, never fails the turn. Gates: 4 tests (`tests/test_wire_prompt_capture.py`); static-path untouched (event presence is the templated-path marker); suite ran 3x+.
- **2c `d27f9e9`** — lifecycle re-verified BY GREP (not docstring): pending->`tool.started` / running->`tool.updated` / completed->`tool.completed` / error->`tool.failed`, with the first-time-only started synthesis (error-only first part still yields started->failed). GAP CLOSED: both stream readers' type switches now end in an else tracing `unknown_part {type, raw}` once per type per turn (300-char cap) — `sweave/harness/ opencode.py` + `specialist_runtime.py:~1881-1885`. Gates: 4 tests (`tests/test_tool_lifecycle_2c.py`) over recorded part shapes through the unchanged detail projector.
- **4c-backend `49f7a6d`** — `JobRunner._run` forwards `on_chunk`/`on_reasoning`/`on_tool` into the runtime (`job_runner.py:1735-1849` + `_attempt` finally close-per-attempt). WS-vocab decision LOCKED: NEW additive names `specialist.delta` / `specialist.thinking` / `specialist.tool` — same shapes as the chat twins keyed by the CHILD delegation_id, no `session_id` (new names over same-name reuse: chat consumers could never sweep child fragments into the orchestrator bubble). Text/thinking coalesce on child-keyed `ChatDeltaCoalescer`s (200ms/64ch); tools latest-status-wins per callID, `MAX_TOOLS_PER_MESSAGE` bounds NEW callIDs. Publishing guarded (never fails the turn). Gates: 4 tests (`tests/test_child_live_forwarding.py`) + `test_job_runner` _StubSpecialistRuntime doubles track the three kwargs.

Frontend half of 4c (`livePoll.ts` ~4s poll while running/queued, idle on settle) + 4a tabbed DetailView + 4b cards landed from the parallel thread (`7d15878`/`d1087bf`/`0e9b67d`) — the additive key name matches the backend lock glyph-for-glyph.

Gates: focused area suites 196 passed (24 amendment tests + 172 neighbors in this window); full pytest 1216 passed at landing (the only red the recorded pre-existing UI-thread failure, invariant under stash); `run.py --check` 13/13 at every step.

Docs: DESIGN §4 row + PROJECT_STATE amendment entry landed in the recovery commit `76de385` (the prior docs-finalize session hit the iteration ceiling there); GOTCHAS entries 10-12 (transcript is a JOIN of two sources; trace prompt capture is sizes+preview; unknown wire part is a row) ride this commit.
