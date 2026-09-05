# R4 — Web UI rebuild (PROJECT HUB)

Status: **restructured 2026-09-05 as a multi-milestone project** (user ruling:
"R4 needs to be handled as a project/undertaking on its own — with detailing and
continuation"). This file is the hub: rulings, quality bar, sub-milestone index,
and the continuation protocol. Each sub-milestone gets its own `docs/R4_X_PLAN.md`,
its own execution, and its own verification round — same loop as M1.

## Rulings (locked 2026-09-05, wave-1 review)
1. **The wave-1 result is rejected.** UI/UX vastly inferior to the v1 vanilla UI;
   v1 was itself inferior to any mainstream agent web UI. The bar is external
   (opencode `session-ui`, LibreChat/LobeChat-class chat UX), not "better than
   nothing".
2. **Project/session distinction is non-negotiable.** Sessions are per-project;
   navigation is a project → session tree (with a project switcher). The wave-1
   shell dropped this — it returns as the navigation backbone.
3. **Scaffold-first.** Every surface ships as a *designed scaffold* (real layout,
   real nav, empty-state design) before it ships with features. Blank stubs and
   missing pages both violate the expectation — scaffolds set it.
4. **Flag-day stands.** No coexistence with the vanilla UI (it is retired in git
   history). Rough edges during R4 are accepted; untracked work is committed at
   each round boundary (the 0443477 preservation commit is the pattern).
5. **Ground rule amended** (2026-09-05): `sweave-web` (Vite + React 18 + TS +
   Tailwind + Zustand + React Query) is the UI source; build output served by the
   backend, not committed.
6. **R4 is run with the same planning/execution method as M1** — plan → confirm →
   execute → verify per sub-milestone; the planner audits; amendments are recorded.

## Known bug (R4.0 scope, found by the user in live use)
Chat turn → `HTTPStatusError 500` posting to `/session/chat-{delegation_id}/message`.
Root cause: the per-Session orchestrator binding seam returned **our internal
delegation id** as the opencode session id instead of the stored `ses_*` id (or its
create-on-first-use fallback fabricated one). Tests missed it because the mocked
harness accepts any id string.

## Quality bar (binding for every R4.x)
- **References**: opencode `session-ui` (tool-part rendering, message composition),
  LibreChat/LobeChat-class chat UX (streaming, markdown, code blocks), v1's theme
  presets (ported as tokens).
- **Scaffold-first**: every page/surface lands as a designed scaffold before
  features fill it.
- **Wire-shape tests**: UI-facing integration tests must emulate the real wire
  (unknown session ⇒ error; ids are `ses_*`), not accept-anything mocks. The M1.9
  mock now emits terminal info on every response — same discipline everywhere.
- **No full-tree re-renders on streaming** (the M1.8 invariant carries over).

## Sub-milestone index (order = execution order; confirm before detailing)
| ID | Scope | Est. | Plan doc |
|---|---|---|---|
| **R4.0** | Hotfix: chat session-id resolution bug (orchestrator_session_id getter/setter audit; create-on-first-use path; never fabricate ids) + wire-shape regression test (mock POST /session returns `ses_*`, unknown-id message ⇒ error) + the `chat-` prefix source found and fixed | ~0.3 | in this hub (small) |
| **R4.1** | UX foundation: project → session tree navigation (project switcher, session list per project), theme system to the external bar (tokens, presets, dark default), app shell redesign, **scaffold-first: every v1 surface exists as a designed stub** (chat, children, detail, memory, agents, settings) | ~1 | `docs/R4_1_PLAN.md` (rewritten — the current R4_1_PLAN.md content moves to R4.4) |
| **R4.2** | Chat surface to the quality bar: streaming (chat.delta), markdown/code rendering, serial-turn indicator, session lifecycle in-thread (closes M1.9 funnel leak #1) | ~0.75 | `docs/R4_2_PLAN.md` |
| **R4.3** | Output funnel to the quality bar: children live tree (already partly in wave 1 — rebuilt to the bar), escalation lane (`ask_human` answer inline), promote inline, delegation detail view (composed prompt, tool timeline, tokens/cost, output — from M1.9's parts-model traces) — closes funnel leaks #2–#4 | ~0.75 | `docs/R4_3_PLAN.md` |
| **R4.4** | Memory pane, Agents workbench, Settings + model catalog (the drafted wave-2 spec, re-cut post-R4.1) | ~1.5 | rename `docs/R4_1_PLAN.md` → `docs/R4_4_PLAN.md` |
| **R4.5** | Dogfood gate: user daily-drives on real work; funnel-leak round 2; friction list → next round (R2 interleave decision happens here) | gate | — |

## R4.0 — Hotfix detail (chat session-id class bug)

Root cause (verified by code read, specialist_runtime.py):
- _build_process() constructs the OpenCodeProcess with
  `session_id=delegation.delegation_id` (for chat turns: `chat-{hex}`) as a
  'placeholder; _ensure_session replaces' — but **`_ensure_session` never touches
  `process.session_id`**: its three paths (create / recreate-on-404 / reuse) only
  update the external binding (`_set(new_id)` = Session.orchestrator_session_id or
  specialist.session_id). The process then POSTs to the placeholder id ->
  opencode 500. Two sources of truth for one id; the wire got the fake one.
- The same placeholder pattern applies to non-orchestrator specialists.

Fix (all paths):
1. `_build_process` passes `session_id=None` (no fabricated id — contract:
  the id is resolved by `_ensure_session`, never invented).
2. `_ensure_session` sets `process.session_id` (and marks the process
  session-created) in ALL three paths: create -> new ses_*; recreate-404 ->
  new ses_*; reuse -> the stored id. The binding and the process can never
  diverge again.
3. `send()` asserts the resolved id is a real serve-issued id (starts with
  `ses_` per the v2 API); a placeholder-shaped id raises instead of hitting
  the wire.

Wire-shape regression test (mandatory):
- Mock emulates the real contract: `POST /session` returns `{id: 'ses_...'}`;
  message POST to a non-`ses_` id returns 500 like the real serve.
- Assertions: chat turn posts to the stored/bound id; second turn reuses the
  same id; a stale binding (404) recreates and rebinds; the internal
  delegation id NEVER appears in any wire URL.

Est. ~0.3 sessions. This fix is a prerequisite for R4.1 review (chat must work
to evaluate the shell).

**R4.0 — done 2026-09-05** (3 commits, ~0.3 session). Three commits:

1. `R4.0 step 1` — `_build_process` seeds `session_id=""` (no fabrication);
   `_ensure_session` writes `process._session_id` in all three paths
   (create / 404-recreate / reuse); `_send_message` asserts the resolved
   id starts with `ses_` before posting. Removed dead
   `_persist_session_id` helper.
2. `R4.0 step 2` — wire-shape mock tightened: `POST /session/{id}/message`
   rejects non-`ses_`/non-issued ids with 500; `GET /session/{id}` returns
   404 for unknown ids (matches the real serve). Mock id format changed
   from `ses-mock-{name}` to `ses_mock_{name}` (v2-faithful `ses_` prefix).
3. `R4.0 step 3` — wire-shape regression test (`tests/test_r4_0_wire_shape.py`,
   5 tests) covering: chat turn posts to resolved `ses_*` (never the
   `chat-{hex}` placeholder); second turn reuses; 404-recreate rebinds;
   the chat-hex prefix NEVER appears on the wire; `_send_message` refuses
   non-`ses_` process ids. Mock plumbed with `_StubStreamResponse.text`
   (for the harness's `HTTPStatusError` catch) and seeds
   `process._session_id` with `ses_mock_{name}` at construction.

**Tests**: 452/452 pytest (was 447; +5 from the wire-shape file), 13/13
`run.py --check`, 40 vitest. The regression test was confirmed to fail
(4/5) when the `_ensure_session` propagation was temporarily reverted
— a real regression test for the original bug. R4.1 is now unblocked.

## Continuation protocol (per sub-milestone)
Planning round (state → consistency → discuss → confirm → detail `R4_X_PLAN.md`)
→ execution session (steps + gates + commits named `R4.X step N`) → planning
verification round (gates, docs reconciliation, amendments absorbed). Same rules
as M1: amendments justified + recorded; user rulings at principle forks; wire-shape
tests mandatory for anything touching opencode.

## History hygiene note
The wave-1 commits were mislabeled `M1.9 step 1-4` (they are R4 steps 1–4; M1.9
closed at `ee094ae`). Recorded here; not rewritten (no remote yet, but the cost/
benefit of rewording commit messages doesn't justify it — the hub is the map).
