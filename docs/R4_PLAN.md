# R4 — Web UI rebuild: sweave-web (execution plan)

Status: planned, not started. Est. ~3–4 sessions for wave 1 + cutover.
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
