# Stats visual polish — fix-up plan of record (2026-09-15)

Status: **detailed** — execution-ready for a frontend session.
Fixes up (not wastes) the timed-out polish attempt
(`1e80e2435270`, `turn_timeout_exceeded_1800s`): the 5-graph
`Stats.tsx` + both Stats test files are dirty in the working
tree, uncommitted, unproven (no build/test run from the planning
lane — no shell here, by the orchestrator read-only ruling).

## 0. Rulings (user-locked 2026-09-15)

1. **Fix up, don't revert.** Keep the 5 graphs; tighten into a
   clean, ordered, high-grade page.
2. **Sloppy = dense + unordered + unclean** (mashed-page feel).
   The fix is information architecture first, pixels second.
3. **UI deps are fine.** The zero-dep SVG ruling is lifted for
   this page only — adopt a real chart/visualization library
   under DESIGN.md §8 (small, maintained, permissive license;
   no framework that owns the agent loop). Candidate: `recharts`
   (MIT, composable, theme-token friendly) vs lighter SVG-kit
   alternatives — executor evaluates against the stack
   (React 19 + Tailwind v4 + radix + lucide already in
   `package.json`) and records the adoption in §8 per policy.
4. **Comprehensive + extendible.** Up to par with any stats page
   (high-grade), structured so future ledger dimensions slot in
   without redesign.

## 1. Starting point (re-verified 2026-09-15 against code)

- Page: `sweave-web/src/pages/Stats.tsx` (668 lines dirty) —
  `DayTrendSparkline` (dual-layer in/out + peak markers + cost
  overlay toggle + hover + aria), `ModelBars`, `DayComposition`,
  `OutcomeDonut`, `ContextHint`, `SplitTable`
  (Name/Turns/In/Peak/Out/Est.cost/Failed), 6-card totals
  (Turns/Input/Peak/Output/Cache/Est.cost), `StatsSkeleton`,
  header + window chip, WS invalidate on
  `delegation.status_changed`. All from the existing
  `StatsSummary` payload — no API change.
- Data: `api.getStatsSummary(days=30)` → `GET
  /api/stats/summary` (`sweave/web/routers/stats.py`), computed
  on read, counts-only. Backend Phase 0/1b landed
  (`sweave/stats/pricing.py` shared fold; ledger cells carry
  `estimated_cost`/`cost_source`/`unpriced`; detail `price`
  fold) — page contract is stable.
- Types: `Tokens{input,output,reasoning,cache_read,cache_write,
  cost,context_input?}` + `StatsCell{turns,failed,
  estimated_cost?,cost_source?,unpriced?}` + `StatsSummary{
  window_days,generated_at,totals+wall_seconds/completed_turns,
  by_day/by_model/by_project/by_agent/by_kind,by_status,
  by_error}` (`sweave-web/src/types/index.ts:270-319`).
- Tests (dirty, pin the 5 graphs): `Stats.test.tsx` (totals /
  splits / errors / empty / skeleton / totals-order / by-kind /
  single-day / pre-1b degrade) + `StatsCosts.test.tsx` (Est.cost
  column, sparkline lines+bars, totals Free/unpriced, overlay
  toggle + hover title, model bars, composition, donut, hint).
- Policy baseline: DESIGN.md §8 (Adopt now: radix + cmdk +
  assistant-ui + agent-elements + shiki + fonts; UI stack:
  React 19 + Tailwind v4). No chart lib today — the adoption
  lands here.
- Docs debt (this plan does NOT fix; separate to avoid
  clobbering dirty DESIGN/PROJECT_STATE/GOTCHAS): DESIGN §4
  ledger row + PROJECT_STATE current-state still say "totals +
  splits" (no graphs); USAGE_LEDGER Phase 1b describes the
  basic sparkline only. Reconcile after the page lands.

## 2. Goal state

A `/stats` page that reads as designed, not mashed: ordered
narrative (headline → trend → breakdowns → details), one
visual language (shared card radius, theme tokens,
tabular-nums), every graph earning its space, and a component
structure the next ledger dimension extends without redesign.
Unpriced-never-$0, Free-vs-unpriced copy, pre-1b degrade, and
stable `data-testid` names all hold.

## 3. Steps

### Step 1 — Architecture + dep (~0.3 session)
- Impose page order: header + window chip → 6 totals →
  Trend (hero, full width) → breakdowns grid (ModelBars +
  DayComposition; Donut + ContextHint) → detail tables
  (collapsible or tabbed per split — tables are reference,
  not narrative) → failure classes.
- Evaluate + adopt ONE chart/visual lib per §8 (record in
  DESIGN §8: name, license check, why). Extract shared
  primitives first: `ChartCard` (title + body + caption),
  `FmtCost`/`FmtTok` (single copy of the Free/unpriced rules),
  `EmptyState`. Keep existing `data-testid` roots stable;
  new graphs get `stats-*` ids.
- Spacing pass: consistent `space-y` rhythm, section
  hierarchy (one `h2` style), responsive grids
  (`xl:grid-cols-2` already; add mobile stacking +
  `overflow-x-auto` tables — already on SplitTable).
- Done-gate: dep installed + §8 recorded; page renders same
  data in the new order; existing vitest green; `npm run
  build` green.

### Step 2 — Graphs to high-grade (~0.5)
- Trend (hero): adopt-lib line/area for in/out + peak
  markers + cost overlay (toggle kept); tooltips over native
  `<title>`; empty/single-point/ar-ia states kept.
- Model bars → adopted horizontal bars w/ value labels +
  truncation + `title` tooltips (long ids).
- Composition → adopted stacked bars w/ legend (already
  captioned; keep in/out/reasoning colors stable).
- Donut → adopted donut w/ center % + succeeded/failed
  legend (keep `stroke-success`/`stroke-destructive`
  semantics).
- ContextHint → keep as explainer card (billed-vs-size rule
  copy), styled as callout not chart.
- Reduced-motion: disable animation under
  `prefers-reduced-motion` (page already claims it — pin it).
- Done-gate: new/updated vitest per graph (render + Free /
  unpriced + single-point + pre-1b shape); build green.

### Step 3 — Polish + tables + gates (~0.3)
- Tables to reference-grade: collapsible sections (default
  open: day + model; collapsed: project/agent/kind) or a
  tab strip — executor picks, one pattern only. Keep the
  Est.cost + Peak columns and title tooltips.
- Header: window chip stays; add `generated_at` relative
  label ("computed Xs ago") — data already carries it.
- Loading skeleton matches new order; error/empty states
  kept; mobile wrap verified (probe: `npm run ui:shot` or
  the ui-chat-probe family pattern).
- Full gates: `npm test` + `npm run build` + `run.py
  --check` green; headless-Edge screenshot over the real
  backend (console + pageerror captured); one commit per
  step (`Stats polish step N: ...`).
- Done-gate: all green + screenshot reviewed; docs-debt
  follow-up filed (DESIGN §4 row, PROJECT_STATE entry,
  USAGE_LEDGER Phase 1b addendum) — separate commits,
  after other threads land.

## 4. Explicit non-goals

- No backend/API/type-shape changes (payload is fixed;
  missing field → escalate, never invent).
- No prompt/response text, ever (ledger boundary).
- No live ticker, no scheduler, no new WS vocabulary
  (existing `delegation.status_changed` invalidate stays).
- No touching `test_out*.txt` scratch (other habit; the
  review-hardening scratch rule covers it).
- No DESIGN/PROJECT_STATE/GOTCHAS edits in build commits
  (dirty by other threads — follow-up only).

## 5. Risks

- Dep weight vs bundle: evaluate size before adopting
  (recharts ~100KB — acceptable for a route-level split;
  pin the import to the Stats route, never global).
- Adopted-lib theming: must accept CSS vars / theme tokens
  (dark presets incl. Carbon default) — verify across 2+
  presets or keep SVG for that graph.
- Test-churn: dirty test files already pin hand-rolled SVG
  shapes — adopting a lib rewrites those pins (expected;
  keep the *behavioral* pins: Free/unpriced, single-point,
  pre-1b degrade, totals order).
- Scope creep into backend (new splits/series): out —
  file as follow-up against `ledger.py`, never in this
  change.

## 6. Execution record

- 2026-09-15: plan detailed (planning session). Timed-out
  attempt `1e80e2435270` contents inventoried; fix-up
  scoped as steps 1–3. Awaiting frontend session.
- 2026-09-16: executed as 3 commits on `master` (fix-up, not revert):
  - `60f6e8f` step 1 — architecture + recharts adoption. Extracted
    `stats/{primitives,charts,tables}.tsx`; page order
    header→6 totals→Trend hero→breakdown grid→donut/context→
    collapsible tables→failures; recharts adopted per §8 (MIT, this
    page only, route-level lazy split — out of the global bundle).
    `ModelBars`/`DayComposition` kept as hand-SVG (recharts' vertical
    `BarChart` paints no bars under jsdom + needs custom truncation/
    tooltips); Trend hero uses recharts; donut hand-SVG. Shared
    `ChartCard`/`FmtCost`/`FmtTok` chrome; reduced-motion respected;
    stable `data-testid` roots kept.
  - `2ec6307` step 2 — per-graph behavioral tests
    (`StatsGraphs.test.tsx`, 8 tests): Free/unpriced, single-point,
    pre-1b degrade, donut center %, model truncation+tooltip,
    context-hint, reduced-motion.
  - `5159934` step 3 — `generated_at` relative chip + collapsible
    reference tables + skeleton/error/empty/mobile; gates green:
    `npm test` 395 vitest, `npm run build` (recharts split),
    `run.py --check` 13/13, headless-Edge screenshot over real
    backend 0 console + 0 pageerror (desktop + mobile).
  - Docs follow-up deferred (plan §4): DESIGN §8 adoption row +
    §4 component row, PROJECT_STATE entry, USAGE_LEDGER Phase 1b
    addendum — separate commits, after other threads land.
