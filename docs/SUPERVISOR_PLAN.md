# Progress supervisor — plan of record

Status: **steps 1–2 built (2026-09-15); steps 3–5 open**.
Parent behav-spec: `docs/REVIEW_HARDENING_PLAN.md` §5. This file
is the execution-ready detail: incident, clock inventory,
rulings, steps, gates.

## 0. Motivating incident (2026-09-15, verified from trace)

Delegation `f774d84b` / `98793101109e` (software-engineer, engine
harness, GIT_READ_TOOL_PLAN implementation): ran 14:45:59 →
15:15:59 (exactly 1800s), HEALTHY throughout (~40 tool
completions — reads, edits, writes, multi-minute pytest runs — +
reasoning pulses, last activity 27s before death). Killed by
`turn_timeout_exceeded_1800s` with record output `""`. File edits
survive in the tree (it `git add`ed at 15:02); the turn's text is
lost. Parent chat turn holds a failed blocking child.

Three findings (each is a spec item below, not just color):

1. **Nested clocks, split ownership.** The turn dies by the FIRST
   of three independent clocks: (a) outer `JobRunner._bounded_turn`
   budget — project-overlay aware (`_turn_budget_for`,
   `job_runner.py:309`); (b) harness httpx `timeout =
   turn_timeout + 30.0` (`harness/engine.py:557`); (c) sidecar
   loop enforcement of `body.turn_timeout`
   (`harness/engine.py:506-515`). The runtime never forwards the
   per-delegation budget into message metadata, so (b) and (c)
   are ALWAYS 1800 (`_turn_timeout` default,
   `harness/engine.py:776-781`) no matter what the overlay says.
   Consequence: raising the fuse via overlay today lifts the
   outer wait only — the inner attempt still dies at 1800. Any
   fuse change without unifying ownership is theater.
2. **Corpse re-arm.** At the expiry second the outer granted
   `turn_extended n:1 (defer_beacon_recent)` and re-armed another
   1800s — over an inner attempt that had already returned the
   `[chat error: turn_timeout: ...]` sentinel. `_bounded_turn`
   never checks `task.done()` before consuming an extension: the
   re-arm waited ~0.2s on a corpse, then recorded the failure.
3. **Missed soft question.** The beacon branch consumed the only
   expiry evaluation, so the keep/stop question never fired. The
   human was never asked about a turn that had pulsed 27s
   earlier.

## 1. Clock inventory (verified 2026-09-15; executor re-verify)

| # | Clock | Owner | Value | Source |
|---|---|---|---|---|
| 1 | Outer turn wait | `JobRunner._bounded_turn` (`job_runner.py:829`) | overlay-aware (`_turn_budget_for`), default 1800 | `routing.turn_timeout_s` (max 14400) |
| 2 | Beacon extensions | same loop (`:952-968`) | max 3 (`MAX_TURN_EXTENSIONS`, `:801`), full-budget re-arms | defer-beacon recency, 300s window (`BEACON_WINDOW_SECONDS`, `:802`) |
| 3 | Soft keep/stop | same loop (`:969-976`, `_ask_soft_limit`) | one question per turn, keep = one full re-arm | first unwitnessed expiry |
| 4 | Inner engine attempt | harness `send` (`harness/engine.py:506`) + sidecar loop | ALWAYS 1800 (metadata default; runtime never forwards) | `message.metadata["turn_timeout"]` (absent) |
| 5 | Inner httpx | same `send` (`:557`) | inner + 30s | derived |
| 6 | Opencode path | `process.send` httpx 1000s + `PRE_MODEL_TIMEOUT_SECONDS` 950 + stall timers | separate stack, out of scope for steps 1–3 | `specialist_runtime.py` |

## 2. Rulings (locked earlier — restated; NEW proposals marked)

Locked (prior rounds): engine-first spec, opencode parity-not-ceiling;
fail loud (trips never auto-retry/resume); soft keep/stop is the
human surface (no new question kind); no new WS vocabulary (trace
+ existing events only); verdicts judge, supervisor watches (clean
seam); iteration trips (budgets/stuckness/doom) stay as the layer
below, complementary.

PROPOSED (need user ruling at the detailing round):

- **P1 — One clock owner (recommended).** The supervisor owns ALL
  time supervision for a turn. Inner layers keep backstops ONLY
  (orders of magnitude above any fuse — e.g. httpx stays as a
  transport backstop) and never an independent kill at the same
  value. Concretely: runtime forwards the per-delegation budget
  into engine message metadata (step 1); the sidecar enforces the
  body value (unchanged code, now correct input). Rationale:
  incident finding 1 — split ownership makes every fuse change
  theater and every extension suspect.
- **P2 — Engine-first buildable NOW (recommended re-sequence).**
  The old sequence gated the supervisor on the view track + full
  streaming. Re-examined: the supervisor's minimum inputs are
  pulses (engine `tool.*` + `reasoning` + `tokens_used`, all in
  trace TODAY), holds (escalation store, TODAY), aliveness
  (serve/port probe, TODAY), abort (sidecar abort + serve abort,
  TODAY). The view track's remainder (live-block UI, transcript
  parity) is human observability, not supervisor input. So: build
  engine-first now; opencode gets longer verification +
  `signal_gap` traces (ferry where cheap). The blockers shrink to
  opencode-parity scope.
- **P3 — Fuse default (hours).** Propose 4h (`14400`, the config
  max) once P1 lands — the fuse becomes a runaway backstop that
  fires only when also pulseless. Detail the exact number at
  execution.
- **P4 — Quiet window 300s starting point**, retunable per role
  (chat snappy, execution patient) at execution.
- **P5 — Trip handoff carries partial text** (capped), not just
  totals/tools/files: on trip the record keeps the last partial
  text instead of `""` (the "never loses the thread" principle
  from turn-cancel). Session continuity already survives via the
  durable engine session; this is about the record.

## 3. Starting point (executor: verify before touching code)

- `_bounded_turn` (`job_runner.py:829-988`): fixed-budget
  `wait_for` windows + hold/soft/beacon branches as inventoried.
- Harness `send` (`harness/engine.py:503-557`): metadata
  `turn_timeout` default 1800, body echo, httpx +30s.
- Runtime engine path (`specialist_runtime.py:595-625`):
  no `turn_timeout` in message metadata (the gap P1 closes).
- Trace vocabulary: `turn_extended` (+reasons), `turn_timeout`,
  `turn_soft_limit_*`, `attention_flag`, `tool.*`,
  `tokens_used`, `verdict_recorded` — reused, no new verbs
  (one exception: `waiting_with_progress`, data not verb).
- TurnStatusBar quiet-Ns + keep/stop card exist (no new UI).

## 4. Goal state

A turn is killed for **pulselessness verified against aliveness**,
never for age alone. The per-turn total is a runaway fuse (hours)
that fires only when also pulseless. Every expiry evaluation
checks completion first (no corpse re-arms), and a dead-but-pulsed
turn routes to the human (keep/stop), never straight to failed.
One clock owner per turn; the overlay fuse works end-to-end (P1).

States/signals per the §5 behav-spec (unchanged, now normative):
HEALTHY / QUIET-WATCH / VERIFYING / HELD / TRIPPED; pulses /
aliveness / holds / runaway; `waiting_with_progress` trace on
alive-verified quiet; trip handoff = totals + tool count + files
touched + partial text (P5).

Precedence (user ruling 2026-09-15 — interrupt is control, not
content): **user interrupt = Stop button > keep/stop verdict >
supervisor trip > fuse.** Interrupt wins in ANY supervisor state
(incl. HELD — a pending question resolves as skipped, mirroring
Stop-button semantics — and VERIFYING — no probe delays a
deliberate stop). An open keep/stop question resolves with the
interrupt (never orphaned). The expiry evaluation checks the
interrupt flag first, alongside `task.done()`: both short-circuit
before any pulse math. Forensics distinguishes sources on the
existing `turn_killed` shape (`source: user_interrupt |
supervisor_trip | stop_button`) — data, not a new verb. Status
stays `failed` with cancelled-class error text (the
`CANCELLED_BY_USER_ERROR` precedent: closed `VALID_STATUSES`
untouched).

## 5. Steps

### Step 0 — Fuse note (config, no code; optional, user call)

Raising `routing.turn_timeout_s` (project overlay, ≤14400) is
NOT effective until step 1 lands (finding 1: inner clocks ignore
the overlay). Do not sell it as a fix before then. After step 1,
it becomes the supported interim posture while steps 2–3 build.

### Step 1 — One clock owner + corpse guard (small, unsupervised)

DONE 2026-09-15 (`a7e453b`): runtime forwards the per-delegation
budget into engine metadata on runner + chat paths; harness
default stays the fallback; `_bounded_turn` + chat wait collect
done tasks immediately (`turn_corpse_collected`); harness
`_turn_timeout` rejects bools. Default behavior byte-identical
(1800=1800).

1. Runtime forwards `_turn_budget_for(delegation)` as message
   metadata `turn_timeout` on the engine path (both
   `_run_engine_attempt` call sites: task turns; chat-turn engine
   path if separate). Harness default (1800) stays as fallback;
   sidecar code unchanged (enforces the now-correct body value).
   Opencode path untouched (separate stack, step 4 scope).
2. `_bounded_turn`: at every expiry evaluation, FIRST check
   `task.done()` — if done, collect immediately without
   consuming an extension, asking, or re-arming (finding 2).
3. Tests: metadata carries overlay value (incl. fallback 1800
   when no resolver); corpse path collects without extension
   (regression pin for this incident: re-arm over a completed
   task is a 0s wait, traced distinctly); overlay raise moves
   both layers (hermetic where possible, else unit on
   `_turn_timeout` + `_turn_budget_for` agreement).
4. Done-gate: new tests green; full pytest green; default values
   byte-identical in behavior (1800=1800 — the race just
   disappears).

### Step 2 — Dead-but-pulsed routes to human (small)

DONE 2026-09-15: pulsed failures (timeout OR wire-death sentinel
— the incident's actual shape) file ONE keep/stop question
(`turn_soft_limit_asked{reason: pulsed_dead_rerun}`); keep =
ONE fresh attempt on the same tree + resumed session
(`turn_soft_keep_rerun`), stop maps to `turn_stopped_by_user`;
silent deaths fail straight; second deaths fail straight (asked
once, globally — `_ask_soft_limit` refuses second filings);
legacy path untouched. Attempt loop unifies result interpret
(single build + sentinel check; shared tail runs once; saver now
also persists failed turns' session bindings). 10 new tests.

1. If the task is done-failed AND showed pulses within the
   beacon window, the expiry path asks the soft keep/stop
   question (when still unasked) instead of failing straight
   (finding 3). Keep = re-defer/retry per existing semantics
   (fail loud stands — keep re-runs, never auto-resumes blind);
   stop/timeout = fail with the handoff.
2. Trace: `turn_soft_limit_asked` with reason
   `pulsed_but_dead` (data, not a new verb).
3. Tests: pulsed-dead asks; silent-dead fails without asking;
   question asked at most once per turn (existing invariant).
4. Done-gate: step-1 gate + soft-limit suite green.

### Step 3 — Pulse supervision (the supervisor proper; engine-first per P2)

1. Replace `_bounded_turn`'s fixed windows with pulse windows:
   wait in slices (propose 60s); each slice checks trace pulses
   (tool transitions, reasoning, token deltas) since last check.
   Pulseless slices accumulate toward the quiet window (P4);
   any pulse resets to HEALTHY.
2. VERIFYING at quiet-window: serve/session aliveness probe
   (sidecar port + session live; opencode: serve probe +
   `signal_gap` trace where the bus is silent). Alive →
   `waiting_with_progress` trace (last tool + elapsed), keep
   watching; dead/unreachable → TRIPPED (loud handoff per P5,
   existing shape + partial text).
3. Holds (pending escalation/question/soft) suspend everything
   (current rule, preserved as-is); hold-aging stays report-only.
4. Iteration trips untouched below (budgets/stuckness/doom).
5. Opencode behavior: longer verification path + `signal_gap`
   admissions; documented gaps, never silent (engine-is-spec).
6. Tests: hermetic pulse-window suite (pulsed-healthy never
   trips at any age; pulseless+dead trips fast with handoff;
   held suspends; alive-quiet waits with progress trace;
   opencode gap traces). Live gate: long healthy engine turn
   past old 1800 unharmed (needs an auth'd window).
7. Done-gate: full pytest 3×, run.py --check, live gate, docs.

### Step 4 — Opencode parity (ferry-or-gap; scoped at its round)

Activity ferry where cheap (bridge-plugin pattern), longer
verification where not, `signal_gap` everywhere silent.
Scoped against what step 3 actually built.

### Step 5 — Fuse retune + close-out (P3, docs)

Multi-hour fuse default, DESIGN/STATE/GOTCHAS, plan status → done.

## 6. Non-goals

- No auto-retry/auto-resume (fail loud stands; keep re-runs
  explicitly through existing paths).
- No new UI surfaces (TurnStatusBar quiet-Ns, lanes, keep/stop
  card reused). No new WS vocabulary.
- No neural/calibration (M2.5's job). No verdict/promotion
  coupling (P-seam: verdicts judge, supervisor watches).
- No opencode-stack rewrite (its timers stay until step 4).
- No change to commit authority, fix rounds, or gotcha plans.

## 7. Risks

- Pulse-window slicing adds wakeups per turn (60s cadence —
  negligible vs model latencies; no hot loop).
- Aliveness probes against a loaded serve can false-negative
  (mitigate: probe-then-wait-one-slice before TRIPPED; never
  single-probe kill).
- Beacon semantics stay during steps 1–2 (extensions still
  exist; step 3 subsumes them — deprecate, don't remove, until
  step 3 is green).
- Long-tail risk the incident names: context growth (~500K
  input at death) will eventually bind before any clock — that
  is compaction's problem (limit-triggered, separate track),
  not the supervisor's; the supervisor must not become a
  context manager by accident.

## 8. Gates (per series method)

Per step: step tests + full pytest + `run.py --check` + 3× green
+ live check where called. Every step leaves API/UI contracts
touched-none (supervision is turn-internals; observers see only
existing trace + status + escalation events).

## 9. Dependencies — the specialist chat (view track, other thread)

The specialist chat (native specialist execution access: transcript
parity, tool timeline, live block + consented abort — the
specialist-view track) is NOT this plan's work: single owner,
no double-build. But this plan consumes its seams, named here
with fallback postures if they land late:

| View-track delivers | Supervisor consumes as | If late, fallback |
|---|---|---|
| Pulse/witness events (tool-started sensor, transcript parity) | VERIFYING evidence + `waiting_with_progress` content | Engine trace events only (`tool.*`, `reasoning`, `tokens_used` — already in trace); progress line shows last tool + elapsed |
| Live block + consented-abort trigger | Human surface for keep/stop context | Keep/stop card carries totals + last tool + handoff (strictly better than today); abort mechanism (sidecar/serve) already exists independent of the trigger |
| Per-tool budget (1200s proposed) | Coordination: per-tool ceiling vs supervisor windows must agree (a tool killed at 1200s mid-healthy-turn is a supervisor-relevant death) | Supervisor treats per-tool kills as pulses-with-failure (visible, classified), never as silence; the lock value is settled jointly at step-3 execution |
| Interrupt trigger (button in the live block) | Supervisor owns HANDLING: single-writer transition (extends the `_user_cancelled` pattern — interrupt and expiry can never double-write), pending-question resolution, `turn_killed{source: user_interrupt}` trace | Trigger without handling is a dead button: if the view track lands first, its endpoint funnels into the same cancel path as Stop (existing `cancel_subtree` semantics) until step 3 wires the supervisor flag check |

P2 clarified: the supervisor *decision loop* builds engine-first
now (its inputs exist); the *oversight surface* follows the view
track. Neither thread blocks the other; the seam table above is
the coupling discipline.
