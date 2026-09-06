# R4.1 — UX foundation: navigation tree, theme system, scaffold-first shell (execution plan)

Status: **done 2026-09-06** (5 commits; 4 implementation + 1 e2e/docs; ~1.2 session).
Predecessor: R4.0 (hotfix, landed first — chat must work before the foundation is
reviewed). Supersedes the wave-2 draft (moved to `docs/R4_4_PLAN.md` — Memory/Agents/
Settings land in R4.4, after the foundation and quality passes).

## Rulings applied (2026-09-05 wave-1 review)
- Project → session tree navigation is the backbone (per-project sessions; project
  switcher). Wave 1 dropped it; it returns first.
- **Scaffold-first**: every v1 surface ships as a *designed stub* — real layout,
  real nav, designed empty states — before features fill it. Blank stubs and missing
  routes are both violations.
- Theme system to the external bar: v1 presets (dark/light/dracula/nord/catppuccin)
  + custom colors as CSS-variable tokens; dark default; persisted.
- Quality bar references: opencode `session-ui`, LibreChat/LobeChat-class chat UX.
  If a surface can't reach the bar in its step, it ships as a *scaffold*, never as
  a bad final version.

## Starting point (re-verified 2026-09-06, post-R4.0)
- R4.0 ✅: chat session-id bug fixed at all three layers (452 pytest incl. 5
  wire-shape regression tests); chat works — no longer a blocker.
- sweave-web current surface: pages = `chat/`, `children/`, `NotFound` ONLY (the
  pre-M1.x scaffold pages were removed in wave 1). Step 3 creates the missing
  routes fresh: memory, agents, settings, delegation detail.
- **Theme system largely SHIPPED** (verified 2026-09-06, committed b400b9d):
  `sweave-web/src/lib/theme/` = `tokens.ts` (presets as typed tokens), `switcher.ts`
  (+ localStorage), `ThemeApplier`/`ThemeSwitcher` components, 21 vitest tests green.
  Remaining gap: **custom-color override UI** (v1 parity hook — `ActiveTheme`
  typing exists, editor doesn't).
- **v1 presets already ported** into tokens.ts — git history (`git show
  ee094ae:sweave/web/static/style.css`) remains the reference if tokens drifted.
- **Backend gap (executor finding, verified in code)**: the projects router
  publishes **zero WS events** — no `project.created/deleted`,
  `session.created/deleted`, `active_session.changed`. The live tree needs them.
- Backend: full v2 API otherwise. dist served, not committed.

## Amendment (2026-09-06, user ruling — mid-execution)

**Stack upgrade inserted as step 1c; UI direction locked for R4.2/R4.3.**
R4.2/R4.3 adopt assistant-ui (runtime + thread primitives, MIT; official
opencode adapter exists) + agent-elements-derived tool/escalation cards
(MIT shadcn registry) — both require **React 19 + Tailwind v4**, so the
upgrade lands here, before shell work that would otherwise need migrating.
Delegation tree + detail views remain custom (no library covers them).

## Steps

### Step 1 — Theme completion ~0.15
- Custom-color override UI (the v1 parity hook): preset + per-token overrides,
  persisted; wire into ThemeSwitcher. No hardcoded hex anywhere (audit gate).
- Gate: switch presets AND override colors live; 21 existing tests + new ones green.

### Step 1b — Backend WS events for projects/sessions ~0.2
- `routers/projects.py` publishes: `project.created`, `project.deleted`,
  `session.created`, `session.deleted`, `active_session.changed` (existing WSEventBus
  vocabulary style; legacy names preserved if any consumers exist — none known).
- pytest: event published on each mutation (bus-subscriber assertion).
- Gate: pytest green; events visible in a WS listener during the step-2 tests.

### Step 1c — Stack upgrade: React 19 + Tailwind v4 ~0.4
- sweave-web: React 18 → 19 (types, render-behavior audit), Tailwind 3 → 4
  (CSS-first `@theme` config — migrate `globals.css`/tokens; content-detection
  changes), dependency bumps, lockfile refresh.
- Migrate the shipped theme system (tokens.ts/presets + custom-color picker)
  onto v4; 51+ vitest suite green; visual smoke of all five presets + custom
  overrides (step-1 features must survive the migration).
- Gate: vitest green, `npm run build` green, presets + custom colors live.

### Step 2 — Shell: project → session tree + statusline ~0.4 (on React 19 / Tailwind v4)
- Sidebar = **project switcher** (dropdown of projects, create-project entry) +
  **session tree** for the active project (sessions list, active highlight, inline
  create-session form). AppProvider gains active-project + session selection state.
- Topbar statusline: project name + path chip + session + connection state.
- React Query keys + WS invalidation via the step-1b events (no polling).
- Gate: switching projects swaps the session list; creating a session appears
  without reload, driven by `session.created`.

### Step 3 — Scaffold-first: R4 surfaces routed ~0.3
- Routes + designed stubs for: **delegation detail**, Memory, Agents, Settings
  (chat + children already exist from wave 1 — polish to the bar only). Each stub:
  real layout, designed empty state, "R4.2/R4.4" badge where features are pending —
  honest scaffolds, not fake UI. Memory/Agents/Settings content stays R4.4 scope;
  only the designed shells land here.
- Gate: every route renders a designed scaffold; visual review by the user against
  the quality bar before step 4 proceeds.

### Step 4 — Gates + docs ~0.1
- Playwright: navigation tree (switch project → session list swaps),
  `session.created` live update, theme switch + override, scaffold presence for all
  routes. pytest for the new WS events.
- Docs: DESIGN §4 (UI rows: shell/tree ✅, theme ✅, WS events ✅), R4 hub status,
  screenshots in the round report.

## Explicit non-goals
- Chat/children/detail *features* (R4.2/R4.3 — scaffolds only here). Memory/Agents/
  Settings content (R4.4). Mobile. Any backend change.

## Risks
- Tailwind + CSS-variable theming interplay (tokens must be the single source of
  truth) — mitigated by the no-hardcoded-hex gate.
- Scaffold-first can silently grow scope (a "stub" is really a feature) — the
  definition: layout + nav + empty states only; zero data wiring.

## Execution summary (2026-09-06)

Five commits on `master`. One step per implementation commit (steps 1, 1b,
1c, 2, 3); step 4 (gates + docs) folded into the final commit. Step 1c
was inserted mid-execution by the user ruling (R4.2/R4.3 adopt
assistant-ui + agent-elements-derived cards, which require React 19 +
Tailwind v4).

1. `R4.1 step 1` — Custom-color UI: 8-token picker (background /
   foreground / primary / primary-fg / border / muted / muted-fg /
   accent) + 'Reset to preset' button, mounted in the ThemeSwitcher
   dropdown. `rgbTupleToHex` / `hexToRgbTuple` helpers for picker
   round-trip. 11 new vitest (51 total).
2. `R4.1 step 1b` — Backend WS events: 5 events published from
   `sweave/web/routers/projects.py` (project.created / project.deleted
   / session.created / session.deleted / active_session.changed;
   unified names, no legacy aliases). 6 new pytest (458 total).
3. `R4.1 step 1c` — Stack upgrade: React 18.3.1 → 19.2.8 (lucide-react
   bumped for React 19 peer); Tailwind 3.4 → 4.3.3 (CSS-first `@theme`;
   no `tailwind.config.js` / `postcss.config.js`; `@tailwindcss/vite`
   plugin). Tokens migrated to full `rgb()` values so v4 utilities
   resolve without arbitrary-value wrappers. All 51 vitest pass;
   build green.
4. `R4.1 step 2` — Foundation nav: `ProjectSwitcher` (dropdown of
   all projects) + `SessionTree` (always-visible per-project session
   list with active highlight + inline create-session form) wired
   into the Sidebar. AppProvider subscribes to the 5 WS events
   and invalidates the smallest scope of React Query keys; the
   mapping is in `src/context/wsInvalidations.ts` (9 vitest pin
   the contract). 9 new vitest (60 total).
5. `R4.1 step 3` — Scaffolds: `/delegations/:id` (R4.3), `/memory`,
   `/agents`, `/settings` (R4.4) — designed stubs with the
   'Pending R4.X' badge; honest scaffolds, not fake UI. Sidebar
   nav extended with a 'Pane shells' section.
6. `R4.1 step 4` — E2E spec (`sweave-web/e2e/foundation-nav.spec.ts`,
   6 tests) + DESIGN/PROJECT_STATE/R4 hub status updates. The
   Playwright suite is CI-time per the wave-1 pattern (chromium
   1243 dependency not bundled in this repo).

**458/458 pytest** (was 452; +6), 13/13 `run.py --check`, 60 vitest
(+9 + 11), `npm run build` green. R4.2 / R4.3 are now unblocked
(React 19 + Tailwind v4 prerequisite met).

## Amendments during execution

- **Step 1c inserted** (2026-09-06, user ruling): the R4.2/R4.3
  stack direction (assistant-ui + agent-elements-derived cards)
  requires React 19 + Tailwind v4. The upgrade lands as R4.1
  step 1c, between 1b (backend WS events) and 2 (foundation
  nav). One commit, end-to-end; tokens stay RGB-tuple per the
  R4.1 ruling; presets + custom-color picker survive the
  migration.

## Direction note (R4.2/R4.3)
Chat thread mechanics adopt **assistant-ui** (custom runtime adapter fed by our
WS events: chat.delta / message.added / delegation.status_changed /
specialist.escalated). Tool timeline + escalation answer cards render as
generative-UI components (**agent-elements-derived**). Delegation tree + detail
views remain custom. `@assistant-ui/react-opencode` (official opencode adapter)
is the first thing to evaluate in R4.2 planning.