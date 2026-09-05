# R4.1 — sweave-web wave 2: Memory + Agents workbench + Settings (spec)

Status: planned (wave 1 shipped 2026-09-05; this is the dogfood-driven
follow-up). Est. ~2 sessions for the three panes + the friction
list to become the next round of input. The plan is a living
document -- it gets re-cut from the wave-1 dogfood friction list
once the user has driven wave 1 on real work.

## Rulings (locked 2026-09-05, wave 1 review)

- **Three panes, in this order**: Memory → Agents workbench → Settings.
  The order is the dogfood handoff: Memory surfaces the orchestrator's
  recall/reflect/retain loop (the cheapest pane; the user can hit it
  daily); Agents workbench is the largest; Settings is the last (the
  catalog picker + routing editor is the most config-heavy; the user
  reaches for it only when the chat surface needs a different model).
- **All three panes land under the wave-1 nav** (Sidebar). The
  Sidebar's current "Chat" + "Children" stays at the top; the new
  panes slot in under a section header (matches the v1 pattern).
  The funnel-leak close-out from M1.9 / R4 step 4 means the chat
  thread is the primary surface; the three new panes are read-mostly.
- **Wave 2 ships behind a wave-1 gate**: the user must have driven
  wave 1 on real work for at least 3 distinct sessions before wave 2
  lands. The wave-2 spec gets re-cut from the friction list at that
  point. The spec below is the **pre-dogfood strawman** -- it
  identifies the surfaces; the dogfood determines which bits ship
  first.

## Step 1 — Memory pane ~0.6

- Read-only pane listing the active memory banks (global /
  project-X / session-Y from the AppProvider's bank list).
- Three columns: recall (search), reflect (synthesise a
  reflection), retain (store a memory). Each column is a form +
  result list.
- WS-pulsed: a `memory.changed` event refreshes the active bank's
  recent list.
- The pane is a M1.2-era parity feature: the v1 vanilla UI had
  these three forms; wave 1 dropped them. The dogfood will say
  whether the user wants the chat to own these (it could; the
  M1.7 transcript system already does memory + git-diff + synthesis)
  or whether the pane is the right home.

## Step 2 — Agents workbench ~0.8

- Two-pane layout: left = specialists grouped by scope (project /
  global / seed), right = specialist detail. The right pane shows
  the specialist's model + system prompt + tools + session_id (M1.2
  v1 parity with the M1.3 session-reuse fields surfaced).
- Idle/running status pill per specialist (M1.3 plumbing; today the
  status is only visible in the WS event stream, not surfaced).
- Inline model switch on idle specialists: a combobox that calls
  `PUT /api/specialists/{name}/model` (M1.2 step 3).
- ▶ Run → child: a "run task" affordance that creates a delegation
  for the selected specialist. The R4-workbench vision from the
  M1.2 era; dogfood determines whether the chat surface already
  covers the use case (it does, via the orchestrator's defer
  tool) and the workbench is a backstop.

## Step 3 — Settings pane ~0.4

- Three sub-panes: Models, Routing, Catalog.
- Models: list + edit the four roles' default model (M1.2
  step 3 surface; M1.5 model-at-request-time is read-only here).
- Routing: list the current rules.yaml rules + add/remove. M1.2
  step 3 / v1 parity; the v1 vanilla UI had this affordance.
- Catalog: a model picker. The old UI_PLAN's "model catalog"
  lands here. Backed by the opencode `models` command output
  (`GET /api/harnesses` already exposes the providers; the
  catalog is a flattened view of those + the v1 presets).
- All three are admin surfaces; no streaming; no WS subscription.

## Step 4 — Wave 2 cutover + dogfood handoff docs ~0.3

- R4.1 status flipped to done in `DESIGN.md` + `PROJECT_STATE.md`
  + `docs/R4_1_PLAN.md` execution summary.
- The dogfood handoff log: every friction entry from the wave-1
  daily-driver window becomes a row in a new `docs/R4_2_PLAN.md`
  backlog (the same protocol as M1.9 → R4).

## Explicit non-goals

- TUI. Mobile-first. i18n. Parallel-turn UI (the chat is serial per
  session per the M1.7 ruling; the workbench is read-mostly). SSE
  live-follow rendering of specialist streams (the detail view
  covers it; the workbench is a backstop). Fanout / cross-review
  (R2). The agent-loop overhaul (R6).

## Risks

- **Memory pane**: the chat surface already does recall + reflect
  via the M1.7 transcript composer; the pane duplicates that. The
  dogfood determines which surface owns it. If chat wins, the pane
  becomes a read-only inspector.
- **Agents workbench**: the M1.2 spec was a v1-era parity feature;
  R4 plan §2 scopes the chat to the daily-driver workflow. If the
  chat covers the use case (it likely does, via the orchestrator's
  defer), the workbench is a backstop and Step 2 ships as a
  minimal read-only pane.
- **Settings pane**: model catalog staleness. The opencode
  `models` command output changes with the opencode install; the
  catalog refreshes on every pane open (no cache).
- **Scope creep**: the three panes are tempting; flag-day-gate them
  against the dogfood friction list. Wave 1 closed every M1.9
  funnel leak; wave 2 is a different conversation.

## Amendment from wave 1 (locked 2026-09-05)

The wave-1 plan called for wave 1 to ship "Memory / Agents
workbench / Settings" as future backlog. The amendment is: the
backlog is now sequenced (Memory → Agents workbench → Settings)
rather than parallel, and the dogfood handoff protocol (Step 4)
is what makes the sequencing real -- the user drives wave 1 for
~3 sessions, and the friction list becomes the wave-2/R4.2 input.
