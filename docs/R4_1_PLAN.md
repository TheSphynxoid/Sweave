# R4.1 — UX foundation: navigation tree, theme system, scaffold-first shell (execution plan)

Status: planned, not started. Est. ~1 session. Predecessor: R4.0 (hotfix, lands
first — chat must work before the foundation is reviewed; R4.0 done 2026-09-05
in commits a2b25f0 → 24af3b5, 452/452 pytest, chat session-id resolution fixed).
Supersedes the wave-2 draft (moved to `docs/R4_4_PLAN.md` — Memory/Agents/
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

## Rulings applied (2026-09-05 R4.1 planning review)
- **Collapse + cite**: theme tokens + ThemeSwitcher + ThemeApplier are
  **already shipped** by R4 wave 1 (M1.9 step 1) — see
  `sweave-web/src/lib/theme/tokens.ts` (5 v1 presets), `ThemeSwitcher.tsx`,
  `ThemeApplier.tsx`, `styles/globals.css` (CSS-variable tokens), and the
  21 vitest tests in `src/lib/theme/__tests__/`. R4.1's work is to **verify
  and extend**: ship the custom-color override UI (the v1 parity hook the
  ThemeSwitcher has a TODO for; `ActiveTheme = { preset, customOverride }`
  is already typed in `lib/theme/switcher.ts` but the editor doesn't
  exist). The topbar statusline is also already shipped (M1.9 step 1,
  `components/Topbar.tsx`) — re-verify, no change.
- **Backend WS events land in R4.1**. `sweave/web/routers/projects.py`
  currently does `create_project` / `delete_project` / `create_session` /
  `delete_session` / `set_active_*` without publishing on the WS event
  bus — the plan's "WS-aware: new/deleted sessions refresh" is broken
  against the current backend. R4.1 step 2 adds:
  `project.created` / `project.deleted` / `session.created` /
  `session.deleted` / `active_session.changed` (one-line per create /
  delete / activate; payload = the resource summary). The Sidebar
  subscribes; React Query invalidates the relevant keys on each event.
- **R4.1 scaffolds foundation nav only**. The plan's old Step 3 listed
  Memory/Agents/Settings as R4.1 scaffolds, but those are R4.4's scope
  per the R4 hub restructure (2026-09-05). R4.1's scaffold-first scope:
  the project switcher in the Sidebar, the per-project session tree,
  the active-session empty state, and the existing 404 page
  (re-verify). Memory/Agents/Settings land in R4.4 with their data
  wiring (R4.4 follows the same scaffold-first discipline).
- **Create-session affordance = sidebar inline form** (not a modal,
  not API-only). A `New session` button under the session tree opens
  an inline form (name input + submit); `api.createSession` → auto
  `setActiveSession`. Matches the v1 vanilla UI's pattern in a
  scaffold-friendly way (no modal stack).
- **Create-project affordance deferred to R4.4** (the project switcher
  is just a dropdown of existing projects in R4.1; project creation
  happens via the API or the v1-era POST /api/projects path until
  R4.4 ships the workbench with full CRUD). Project creation in
  the UI is a low-frequency operation; not blocking daily driving.

## Starting point (verified 2026-09-05, re-read from disk)
- **Stack**: `sweave-web/` (Vite + React 18 + TS + Tailwind + Zustand +
  React Query); `npm run build` clean (323KB JS gzipped); vitest 40
  tests pass; Playwright 2 e2e tests pass.
- **Theme tokens**: shipped. `styles/globals.css` (106 lines, CSS
  variables for dark + light + 5 v1 presets via `data-theme`
  attribute); `lib/theme/tokens.ts` (preset definitions);
  `lib/theme/switcher.ts` (ActiveTheme type with
  `{ preset, customOverride }`); `components/ThemeApplier.tsx`
  (data-theme attribute applier); `components/ThemeSwitcher.tsx`
  (dropdown, 5 presets, localStorage persistence). 21 vitest tests
  in `src/lib/theme/__tests__/`.
- **App shell**: shipped. `App.tsx` mounts QueryProvider → WSProvider
  → AppProvider → BrowserRouter; `Layout.tsx` renders Sidebar +
  Topbar + Outlet; `NotFound.tsx` exists. `Sidebar.tsx` has 2 nav
  links (Chat, Children) — **no project switcher, no session tree**.
  `Topbar.tsx` shows project + path + session + orch-session-id +
  WS conn state — **complete**.
- **AppProvider**: shipped. `context/AppProvider.tsx` has
  `activeProject` / `activeSession` (sourced from
  `getActiveProject` / `getActiveSession` — server-side pointers);
  `setActiveProject` / `setActiveSession`; notifications. **No
  project list, no per-project session list, no create helpers.**
- **Pages**: `Chat.tsx` (input funnel, streaming + session picker),
  `Children.tsx` (output funnel, live tree + detail view) — both
  work today (R4 wave 1). `NotFound.tsx` exists.
- **API client**: `api.listProjects()`,
  `api.createProject(body)`, `api.listSessions(projectName?)`,
  `api.createSession(body)`, `api.getActiveSession()`,
  `api.setActiveSession(id)` — **all live**, just unused by
  Sidebar/Topbar/AppProvider beyond the active pointers.
- **Backend**: full v2 API + WS vocabulary (M1.9 state). R4.0
  fixed the chat session-id resolution. **Gap**: `projects.py`
  router does not publish WS events on create/delete/activate.
  Vanilla UI retired; `sweave-web/dist` served.

## Steps

### Step 1 — Custom-color override UI + theme verification ~0.2
- **Verify the shipped theme system**: re-run `npm test` (21
  theme tests pass); visual smoke that all 5 presets work
  (the e2e `theme switcher changes the data-theme attribute`
  test covers dark → nord; extend to all 5).
- **Custom-color override editor**: a new "Customize" entry in
  the ThemeSwitcher dropdown opens a popover with the 7
  v1-color tokens (background / foreground / muted / border /
  primary / destructive / accent) + a reset-to-preset button.
  The override persists in localStorage under the existing
  custom-override key (`lib/theme/switcher.ts` already loads /
  saves it — the editor is the missing UI). The
  `data-theme="custom"` attribute is set when an override is
  active; `lib/theme/tokens.ts` already defines the
  `applyCustomOverride` helper.
- **Vitest**: 3-4 new tests for the editor (override persists,
  reset-to-preset works, applying the override updates the
  document style).
- **Gate**: `npm run build` clean; vitest 43-44 tests pass
  (40 → 43-44); e2e `theme switcher changes the data-theme
  attribute` extended to all 5 presets.

### Step 2 — Project switcher + per-project session tree (the nav backbone) ~0.5
- **Backend WS events** (one commit before the UI work): add
  `project.created` / `project.deleted` to
  `sweave/web/routers/projects.py::api_create_project` /
  `api_delete_project`; `session.created` /
  `session.deleted` to `api_create_session` /
  `api_delete_session`; `active_session.changed` to
  `api_set_active_session` (and the equivalent for
  `active_project.changed`). Payload: the resource summary
  (project name + path; session id + name + project_name).
  One pytest covers each event (mock the event bus subscriber
  and assert the event fires on the route).
- **AppProvider extensions**:
  - `projects: ProjectSummary[]` (RQ query: `["projects"]`,
    queryFn `api.listProjects()`, 30s stale time, manual
    invalidation).
  - `sessions: SessionSummary[]` (RQ query:
    `["sessions", activeProject?.name ?? null]`, depends on
    activeProject; queryFn `api.listSessions(activeProject.name)`;
    30s stale time; manual invalidation).
  - `setActiveProject` already exists; extend to also
    `qc.invalidateQueries({ queryKey: ["sessions"] })` (switching
    projects refetches the per-project session list).
  - WS subscriptions: `project.created` / `project.deleted`
    invalidate `["projects"]`; `session.created` /
    `session.deleted` invalidate `["sessions", <project>]`
    (project name from the event payload); `active_session.changed`
    and `active_project.changed` refresh the active pointers
    (already in the AppProvider today; rewire to listen to
    events instead of / in addition to the initial fetch).
- **Sidebar** (`components/Sidebar.tsx`):
  - **Project switcher**: a dropdown at the top of the
    Sidebar (where the "Sweave" wordmark is today), showing
    the active project name + chevron. Click opens a menu
    listing all projects (active highlighted), with a
    "Switch to <project>" action that calls
    `setActiveProject`. **No "create project" entry** —
    deferred to R4.4 per the ruling.
  - **Session tree**: a section under the project switcher
    with the heading "Sessions" + a `+` button. The list
    shows every session for the active project (RQ
    `["sessions", activeProject.name]`), with the active
    session highlighted (clicking it calls
    `setActiveSession(id)`). Clicking the `+` button opens
    an inline form (ruling #4): a single text input
    (`name`) + a submit button + a cancel button. Submit
    calls `api.createSession({name, project_name: activeProject.name})`
    then `setActiveSession(newSession.id)`. Empty state:
    "No sessions yet — create one to start.".
  - **The existing Chat / Children nav links** stay (they
    are the route surface; the Sidebar's two halves are:
    nav-top = project + session, nav-bottom = page routes).
  - Visual hierarchy: project switcher (top, primary
    action), session tree (middle, scrollable if many),
    page nav (bottom, the existing 2 links), connection
    state (very bottom, where it is today).
- **Topbar**: the existing statusline now also shows the
  **session count** for the active project (e.g.
  "12 sessions") next to the project path — small UX
  improvement that surfaces the new state. Optional
  polish, ship if cheap.
- **Vitest**: 2-3 tests for the AppProvider's RQ integration
  (project list refetches on `project.created` event;
  session list refetches on `session.created`; switching
  projects invalidates the per-project sessions query).
  Test the AppProvider's WS subscription wiring in
  isolation (mock the WS subscriber).
- **Playwright**: 2-3 e2e tests:
  - `project switcher dropdown lists all projects and
    highlights the active one`
  - `switching project swaps the session list`
  - `create session from sidebar inline form`
- **Gate**: `npm run build` clean; vitest 45-47 tests pass
  (43-44 → 45-47); e2e 4-5 tests pass (2 → 4-5); pytest
  +5 (the WS-event tests).

### Step 3 — Scaffold-first for R4.1 surfaces ~0.1
- **R4.1's scaffold-first scope is small** (ruling #3):
  the project switcher, the per-project session tree, and
  the active-session empty state are already real
  components after Step 2. The 404 page is already shipped
  (`NotFound.tsx`). What's missing is **empty-state
  polish**: a designed empty state for "no active project"
  (the AppProvider has no projects — show a "Create a
  project via the API or the workbench (R4.4)" message),
  and a designed empty state for "no active session"
  (already a simple message today; upgrade to a real
  design with the new session tree's empty state style).
- **Visual review by the user** (screenshots or live)
  against the quality bar before step 4. This is the
  ruling: "If a surface can't reach the bar in its step,
  it ships as a scaffold, never as a bad final version."
  For R4.1 the surfaces are the project switcher, the
  session tree, the empty states, and the 404 page.
- **Gate**: visual review pass + the e2e tests from
  Step 2 cover the routes (`/`, `/chat`, `/children`,
  `/unknown`).

### Step 4 — Gates + docs ~0.1
- **Full gates**: `pytest -q` (457+ expected: 452 + 5
  WS-event tests); `run.py --check` (13/13, unchanged);
  `cd sweave-web && npm test` (45-47 vitest); `cd sweave-web
  && npm run build` (clean); Playwright (4-5 e2e).
- **Docs**: DESIGN §4 (UI rows: shell/tree ✅, theme ✅
  already, custom-color editor NEW); R4 hub status:
  flip R4.1 from planned to done; PROJECT_STATE this
  entry; `docs/GOTCHAS.md` (any new gotchas, grouped by
  branch — likely a "WS event taxonomy" gotcha for the
  new project/session events; maybe a "RQ invalidation
  pattern" gotcha if the implementation surfaces a
  recurring pitfall).
- **Screenshots**: 2-3 screenshots of the new shell
  (project switcher open, session tree populated, custom
  color editor open) attached to the round report in
  `docs/round_reports/` (create the directory if it
  doesn't exist; the executor stores round artifacts
  here per M1.7 step 4's pattern).

## Explicit non-goals
- Chat/children/detail *features* (R4.2/R4.3 — scaffolds
  only here; the existing pages are R4 wave 1's output
  and stay as-is).
- Memory/Agents/Settings *anything* (R4.4 — not in R4.1;
  no stubs, no nav entries, no routes).
- Mobile. Backend beyond the WS events. Project-creation
  UI (R4.4). Server-side / API pagination on the session
  list (the per-project session list is bounded in
  practice; pagination is R4.4). The session search /
  filter (R4.4). The session rename UI (R4.4). The
  session delete UI from the sidebar (R4.4 — only the
  create UI ships in R4.1).

## Risks
- **WS event taxonomy** (the new events must be named
  consistently with the existing names; `specialist.created`
  / `agent_created` is the precedent — we follow
  `project.created` / `session.created` / `active_session.changed`).
  Mitigation: pytest asserts the exact event name + payload
  shape; one mock subscriber per event.
- **RQ key invalidation timing**: switching projects
  invalidates the per-project session list, but the new
  session list is fetched asynchronously — a brief
  empty-state flash is possible. Mitigation: the
  AppProvider tracks the "active project changed"
  transition and clears the sessions list synchronously
  before the refetch lands.
- **Scaffold-first can silently grow scope** — the
  definition: layout + nav + empty states only; zero
  data wiring (R4.2/R4.3/R4.4 own the data wiring for
  their surfaces). Step 2's sidebar inline form for
  create-session *is* data wiring (one call); that's
  the limit. Step 3 does not add any new data wiring.
- **The custom-color editor is a UI surface, not a
  feature** — but it is real (sliders / color pickers
  for 7 tokens). Scope discipline: ship the editor as
  7 inputs + reset, no live preview beyond the apply,
  no per-component overrides.
- **Project switcher in the Sidebar might be too tall**
  on small viewports (collapsed sidebar with a project
  dropdown). Mitigation: the Sidebar's collapsed mode
  (16-wide) hides the project switcher entirely (the
  Topbar's project name is the affordance to expand).
  R4.4 may revisit the collapsed mode if the user
  pushes back.
