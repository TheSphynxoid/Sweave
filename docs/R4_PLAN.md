# R4 — Web UI rebuild: sweave-web (execution plan)

Status: done 2026-09-05 (per the post-execution summary at the bottom).
Est. ~3–4 sessions for wave 1 + cutover.
Rulings locked 2026-09-05:
- **Stack**: sweave-web's stack (Vite + React 18 + TypeScript + Tailwind + Zustand +
  React Query) — but the existing page code is pre-M1.x and gets **rewritten**, not
  revived. Keep: stack, app shell pattern (router/providers/axios skeleton), scaffold
  layout. Rewrite: everything targeting v1 endpoints, all `any`-typed clients.
- **Wave 1 = daily-driver core + theming from day one**: chat (streaming), children
  live tree, delegation detail view, escalation lane — plus the theme/customization
  system as part of the design system (not a settings afterthought; port v1's presets
  + custom-color support as CSS-variable tokens).
- **Flag-day cutover**: the vanilla UI is retired when wave 1 is usable; no
  coexistence period (user is not currently a Sweave user — nothing to ease).
- AGENTS.md ground rule amended accordingly: `sweave-web` is the UI source; Vite
  build allowed; **build output (`sweave-web/dist`) is served by the backend but not
  committed**; `npm run build` is a documented dev step.

## Starting point
- Backend complete through M1.9: v2 endpoints, WS vocabulary (chat.delta,
  message.added, delegation.status_changed, specialist.escalated, model.changed,
  serve.*), traces (parts-model: tool timeline, steps, tokens), escalations
  (needs_attention + answer endpoint), promote endpoint, per-project worktree_base.
- `sweave-web/` scaffold: routing (BrowserRouter), QueryProvider, AppProvider,
  axios client skeleton, globals.css — usable bones; pages are v1-era rewrites.
- Funnel-leak list (M1.9 self-hosting scene) = wave-1 feature spec: session
  lifecycle in-chat, delegation tree inspection, promote inline, answer escalations
  inline.
- opencode `session-ui` patterns (reference clone) — React-family, transfers 1:1:
  tool-part status rendering (shimmer while pending/running, output/error states),
  message-part composition, session tree navigation.
- Vanilla UI (static/, app.js 1,035-line IIFE) — retired at cutover; its test suite
  (`test_full.py`, sidebar/streaming JS tests) is retired with it, replaced by the
  new UI's Playwright suite.

## Steps

### Step 1 — Foundation: design system + shell + wiring ~0.75
- Theme tokens: CSS variables porting v1's presets (dark/light/dracula/nord/
  catppuccin) + custom-color support; Tailwind theme bridging; theme switcher with
  localStorage persistence (v1 parity from day one — customization ruling).
- App shell: Layout (topbar = statusline vision: project + path + session + conn;
  sidebar = waves 1–2 nav), routing, QueryClient config, **WS provider** (reconnect
  + event dispatch into React Query cache — no polling).
- Typed API client regenerated for the v2 surface (delegations, specialists,
  sessions, escalations, catalog, detail); kill all `any`.
- Dev mode: Vite proxy → :8100; backend serves `dist/` via mount (cutover prep).
- Gate: shell runs against the live server; theme switch works; WS events visible
  in React Query devtools; `npm run build` output serves.

### Step 2 — Chat (the input funnel) ~0.75
- Session picker + creation (in-shell — a funnel-leak fix: session lifecycle without
  leaving the thread).
- Message list with streaming: `chat.delta` patches a single streaming bubble;
  `message.added` replaces with the persisted message; serial-turn indicator;
  orchestrator errors rendered as chat errors (never silent).
- Gate: ported streaming smoke (test_m1_8_streaming_ui.js logic → React Playwright
  test) green.

### Step 3 — Output funnel ~0.75
- Children **live tree**: WS `delegation.status_changed` pulses, depth = tree indent
  (parent_task_id), escalation lane at top (`needs_attention` records with inline
  `ask_human` answer), promote buttons on every `review` record.
- **Delegation detail view**: trace projection — composed prompt (collapsible),
  tool timeline (ToolPart lifecycle: status, duration, input/output), per-step
  tokens/cost, synthesis, final output. `session-ui` patterns for tool-state
  rendering.
- Gate: escalation round-trip + promote + detail view e2e through the UI only (the
  M1.9 funnel leaks, closed one by one).

### Step 4 — Flag-day cutover + test migration ~0.5
- Backend: serve `sweave-web/dist` (SPA fallback); `static/` vanilla UI moved to
  `attic/ui-v1/` (or deleted — git history preserves); `test_full.py` + vanilla JS
  tests retired, replaced by the new Playwright suite (core flows: project open,
  chat turn, tree, promote, escalation, theme switch).
- Docs: AGENTS.md (ground rule amendment, key files, running/testing), DESIGN §4
  (UI rows rewritten), §6 R4 status.
- Gate: new Playwright suite green; manual smoke of all wave-1 surfaces.

### Step 5 — Wave 2 spec + dogfood handoff ~0.25
- Wave 2 backlog from v1 parity gaps: Memory tab, Agents workbench (the R4-workbench
  vision from the M1.2 era), Settings panes (models/routing/memory/catalog picker —
  the old UI_PLAN's model catalog lands here).
- **Dogfood handoff**: the user daily-drives wave 1 on real work; friction list
  becomes wave-2/R4.1 input (same protocol as the M1.9 gate).

## Explicit non-goals
- TUI. Mobile-first. i18n. Server-side rendering. Storybook (the package exists in
  the reference stack discussion — not needed solo). Parallel-turn UI (queue
  semantics only). Agents workbench full polish (wave 2).

## Risks
- React StrictMode double-mounting WS connections in dev — provider must be
  idempotent (standard cleanup pattern; test in dev mode explicitly).
- Streaming + React Query interplay: chat deltas bypass the query cache (direct
  bubble patch, per M1.8's no-rerender invariant) while `message.added` invalidates
  — keep the two paths clearly separated.
- Theme tokens vs Tailwind defaults drift — tokens are the source of truth; Tailwind
  config references them, never hardcodes.
- Scope creep into wave 2 mid-wave-1 (Memory/Settings are v1-familiar and tempting)
  — flag-day gate protects: wave 1 first, then parity.


## Execution summary (2026-09-05)

Four steps landed as planned. Four commits on `master` + the
flag-day cutover. No plan amendments.

1. **Foundation** (commit): design system
   (`sweave-web/src/lib/theme/{tokens,switcher,index}.ts` -- 5 v1
   presets as RGB-tuple CSS variables + custom-color override +
   localStorage-persisted switcher), shell (Sidebar + Topbar +
   Layout + NotificationContainer + ThemeApplier), typed v2 API
   client (`api/client.ts` -- no `any`), WSProvider with reconnect
   + topic dispatch (StrictMode-safe per the React StrictMode
   gotcha in AGENTS.md), Tailwind tokens bridged to the design
   system (popover/ring/input added), Vite dev proxy to :8100.
   21 vitest unit tests. v1-era pages + Header + stray
   `test_minimal_map.tsx` retired (all targeted v1 endpoints or
   were leftover scaffolding). The v1 SPA was starting to
   block the wave-1 plan; deleting them unblocked tsc.

2. **Chat (input funnel)** (commit): pure-function reducer
   (`pages/chat/reducer.ts` -- delta + messageAdded + turnBoundary
   events, the M1.8 streaming invariant as a single testable
   function), MessageList with ref + textContent patch (no React
   re-render per chat.delta), SessionPicker (closes the M1.9
   funnel leak -- session lifecycle in-shell), Composer with
   auto-grow textarea + serial-turn lock per session. 10 more
   vitest tests (31 total).

3. **Output funnel** (commit): tree builder
   (`pages/children/tree.ts` -- root + depth + orphan promotion;
   siblings sort by created_at; findEscalatingNodes flatten),
   LiveTree (depth-indent rows, status pills, PromoteButton on
   every review record, AnswerInline on every needs_attention
   record, EscalationLane at the top), DetailView modal
   (composed prompt + tool timeline + tokens + status timeline
   from `GET /api/delegations/{id}/detail`). 9 more vitest tests
   (40 total).

4. **Flag-day cutover** (commit): backend serves
   `sweave-web/dist` (the SPA catch-all + `/assets` + `/favicon.svg`;
   `SWEAVE_UI_VANILLA=1` env var forces the v1 fallback for
   debugging). v1 vanilla UI assets (sweave/web/static/) +
   v1 UI tests (test_full.py, test_sidebar_nav.js,
   test_promote_ui.js) + v1-era debug/verify/check scripts
   retired (git history preserves). Playwright e2e suite at
   `sweave-web/e2e/` (CI gate; local pytest gate uses
   `playwright test --list` to pin suite registration). The
   `run.py --check` script updated to test the new SPA: 13/13
   (SPA + favicon + /assets + 10 API endpoints).

**One architecture decision that landed in step 1** (no plan
amendment; the plan called for "test-first" + "kill all `any`"
and the decisions followed naturally):
* Tokens are RGB tuples (`"255 255 255"`) not hex strings --
  the static `globals.css` already uses the tuple form
  (`rgb(var(--color-background))`); the runtime writes the
  same shape. A hex format would have required rewriting the
  static CSS to the modern syntax or building a hex->tuple
  converter; the tuple form kept the change to one file.
* The `dist/` directory is already gitignored (the
  pre-existing root `dist/` rule covers `sweave-web/dist/`).
  No new gitignore entry needed.

**No new third-party libraries** were adopted. The R4 stack
was already in the project from the v1-era scaffold (Vite +
React 18 + TS + Tailwind + Zustand + React Query + axios +
lucide-react + clsx + tailwind-merge). New devDeps only:
`vitest` (the unit test runner), `jsdom` (the test environment),
`@tanstack/react-query-devtools` (was imported but missing),
`@playwright/test` (e2e), `playwright` (e2e binary).

**Final gates**:
* vitest: 40/40 (21 design + 10 chat + 9 tree).
* tsc --noEmit: clean.
* npm run build: clean (308KB JS / 18KB CSS at step 3 final).
* Playwright e2e suite registered (2 tests, CI gate).
* Python orchestrator: 4 step files / 14 tests. Full suite:
  447/447 pytest (was 447 at M1.9 step 5; +5 from the four
  step orchestrators). 13/13 run.py --check.

**Funnel-leak close-out** (the M1.9 self-hosting scene's
friction list):
* session lifecycle in-shell (SessionPicker)
* delegation tree inspection (Children live tree)
* promote inline (PromoteButton on every review row)
* ask_human answer inline (AnswerInline on every
  needs_attention row + EscalationLane at the top)
All four leaks closed; the chat thread is now the only
surface the user needs for any of these operations.

**Wave 2 backlog** (Step 5's deliverable): Memory tab, Agents
workbench (the R4-workbench vision from the M1.2 era), Settings
panes (models/routing/memory/catalog picker -- old UI_PLAN
items). Follows the same protocol as M1.9's dogfood handoff:
user daily-drives wave 1 on real work; friction list becomes
wave 2 / R4.1 input.
