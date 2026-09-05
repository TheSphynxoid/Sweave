# R4.1 — UX foundation: navigation tree, theme system, scaffold-first shell (execution plan)

Status: planned, not started. Est. ~1 session. Predecessor: R4.0 (hotfix, lands first
— chat must work before the foundation is reviewed). Supersedes the wave-2 draft
(moved to `docs/R4_4_PLAN.md` — Memory/Agents/Settings land in R4.4, after the
foundation and quality passes).

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

## Starting point
- sweave-web stack live (Vite/React18/TS/Tailwind/Zustand/RQ); wave-1 pages exist
  but: no project/session distinction, no theme tokens (globals.css is 80 bare
  lines), no scaffold discipline, chat currently broken by R4.0's bug.
- Backend: full v2 API + WS vocabulary (M1.9 state). Vanilla UI retired; dist served.

## Steps

### Step 1 — Design tokens + theme system ~0.3
- `styles/tokens.css`: CSS variables per v1 preset (--bg, --fg, --panel, --border,
  --red, --muted, accent set) + custom-color override hooks; dark default.
- Tailwind config references tokens (never hardcodes); ThemeSwitcher (v1 parity) +
  localStorage persistence; document class-name conventions.
- Gate: all five presets switchable live; no hardcoded hex outside tokens.css.

### Step 2 — Shell: project → session tree + statusline ~0.4
- Sidebar = **project switcher** (dropdown of projects, create-project entry) +
  **session tree** for the active project (sessions list, active highlight, create
  session). The AppProvider gains active-project + session selection state (WS-aware:
  new/deleted sessions refresh).
- Topbar statusline: project name + path chip + session + connection state.
- React Query keys + WS invalidation for projects/sessions (no polling).
- Gate: switching projects swaps the session list; creating a session appears
  without reload; WS events refresh state.

### Step 3 — Scaffold-first: all surfaces routed ~0.3
- Routes + designed stubs for: Chat, Children, Delegation detail, Memory, Agents,
  Settings (each with real layout, empty-state design, "wave N" badge where features
  are pending — honest scaffolds, not fake UI).
- 404 page. Nav order matches the funnel priority (Chat, Children, then wave-2
  panes).
- Gate: every route renders a designed scaffold; visual review by the user
  (screenshots or live) against the quality bar before step 4 proceeds.

### Step 4 — Gates + docs ~0.1
- Playwright: navigation tree (switch project → session list swaps), theme switch,
  scaffold presence for all routes. pytest untouched (backend only).
- Docs: DESIGN §4 (UI rows: shell/tree ✅, theme ✅), R4 hub status, screenshots
  attached to the round report.

## Explicit non-goals
- Chat/children/detail *features* (R4.2/R4.3 — scaffolds only here). Memory/Agents/
  Settings content (R4.4). Mobile. Any backend change.

## Risks
- Tailwind + CSS-variable theming interplay (tokens must be the single source of
  truth) — mitigated by the no-hardcoded-hex gate.
- Scaffold-first can silently grow scope (a "stub" is really a feature) — the
  definition: layout + nav + empty states only; zero data wiring.
