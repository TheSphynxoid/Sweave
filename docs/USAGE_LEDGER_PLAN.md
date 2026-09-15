# Usage ledger — local-first product analytics + training fuel

Status: detailed (2026-09-14) -- Phase 0 (shared pricing helper) +
Phase 1b (stats graphs + costs). Surface 2 DONE (`GET
/api/stats/summary` + `/stats` page: totals + per-day / model /
project / agent / kind / status / error-class cells, computed on
read): `sweave/stats/ledger.py` + `sweave/web/routers/stats.py` +
the `/stats` page exist; `render_price` exists nowhere yet (verified
on current master: no `render_price` def in `ledger.py` or
`detail_view.py`).

Source hygiene landed with it: exec-tool outputs capped at
32K before history (only bash was capped — a 607K-char read caused
~2M of a 5.4M-token turn) and the loop-turn `tokens_used` anchor
reports real `cached_tokens` (was hardcoded 0). CLI (`sweave
stats`) deferred — the page covers the read path. Compaction stays
limit-triggered future work (user ruling 2026-09-14: never blind).

User-asked: the trace-measured
timeout-rate finding (mode flip, free-tier correlate, volume
collapse) proved ad-hoc stats answer real questions. Productize
that: a standing usage-data surface, local-first (not against
our principles — nothing leaves the machine unless explicitly
exported), feeding estimation calibration (M2.5), the query
planner's pricing, the context audit, and the training side-thread
(reward/cost signals). Most harnesses don't do this: provider
dashboards (OpenRouter-style) see everything by construction;
external observability (LangSmith/Langfuse) is a paid sidecar;
CLI stats (where they exist) are ephemeral. A built-in,
per-delegation-attributed, local ledger is the gap.

## Design rule: derive, don't instrument

Everything needed already persists: trace `tokens_used` (+ cost),
delegation records (agent/model/status/depth/blocking/estimate/
error class/review verdicts/promotions), override log (routing
gold), escalation records (question kinds + outcomes). The ledger
is a PROJECTOR (the `detail_view.py` precedent): no new hot-path
writes, no schema migration, no turn-latency cost. Recompute is
always possible because the sources are append-only.

## Second tier (2026-09-12): in-agent hooks, extensible collectors

Derivation covers the built-in ledger. For anything custom, the
sweave-agent path (custom engine first, opencode path via the same
vocabulary) exposes a hook surface — cheap because integrated:

* **One event vocabulary, both engines.** The trace event names
  (`tool.*`, `step.boundary`, `tokens_used`, `stalled`,
  `review_requested`, `status_changed`, ...) ARE the hook schema,
  versioned like the wire (wire-drift doctrine extended to events).
  Hooks attach to the existing `WSEventBus` + trace writer — a thin
  registry, not a rewrite.
* **Subscribe with filters**: `on(event, filter) -> collector`.
  Unregistered hooks cost one `if subscribers:` check on the hot
  path; registered ones pay dispatch over already-constructed event
  dicts. High-frequency streams (`chat.delta`) excluded by default,
  sampled on explicit opt-in.
* **Collectors + sinks are user code**: a collector is a small
  record/reduce function; sinks are local JSONL (default), file
  export, or HTTP webhook — aggregate wherever they want. The
  built-in ledger itself becomes the reference collector, not a
  special case.
* **Overhead budget pinned**: hook dispatch time traced per turn
  (same audit discipline as tokens); a hook that blows its budget
  is disabled with a warning, never allowed to wedge a turn.
* **Privacy tiers**: ledger-grade stream carries counts/shapes only
  (export-safe by construction). Raw content (prompts, tool I/O)
  needs explicit per-hook opt-in — the anti-list applies (never
  silent exfil); a raw hook without consent fails closed.

## What's collected (dimensions × measures)

Dimensions: day, project, specialist, model (+variant), role,
kind (task/chat), depth, blocking. Measures per cell: turns,
tokens in/out/reasoning, cost, wall seconds, outcomes
(done/review/failed + error classes), estimates vs actuals
(bias + absolute error), deferrals (fanout width, chain depth),
questions (ask_human kinds, answer/skip rates, permission
allow/deny), promotions (human vs automated), stall/wire-death
counts by phase. Explicitly NOT collected: prompt/response TEXT
(counts and shapes only — the ledger must stay safe to show and
cheap to keep).

## Surfaces (phased)

1. `sweave stats` CLI (record-only read side; same projector the
   HTTP layer would use). Per-day table + model + outcome splits.
   First shippable; proves the projection math.
2. `GET /api/stats/summary` (+ `.../series` for sparklines later).
   Powers a Plan-board-adjacent widget and the estimation
   calibration read path (M2.5 consumes, not builds, this).
3. Opt-in export (the aggregated-DB twin): explicit consent,
   redacted shape (the collected set above IS the exportable set —
   designed export-safe from day one; no transcript text can leak
   because it is never collected), versioned format for the shared
   test-bed + trajectory corpus.

## Explicit non-goals

- No hot-path writes, no new delegation/session fields.
- No prompt/response text collection, ever (boundary, not backlog).
- No scheduler/daemon for rollups (compute on read; cache
  optionally later with invalidation, never a cron).
- No R4.4 dependency (memory sections read as zero until the
  backend lands — same honesty as the audit).

## Risks

- Double-counting across overlapping windows (turns spanning
  midnight UTC; retries creating sibling records): define the
  attribution rule in code (created_at day; retries linked by
  parent, not merged) and pin with tests, or every number is
  suspect — the bugs-lane merge-rule precedent applies.
- Billed-vs-size (2026-09-15 incident: one 7-step chat turn moved
  the ledger 1.7M → 4.8M): per-step prompts re-bill full history,
  so `tokens_used.input` is the billed sum (steps×context) while
  `context_input` is the peak live context (max). Ledger cells sum
  the former and max the latter; compaction triggers must read the
  peak, never the sum. Pre-split traces contribute 0 peak (never
  invented).
- Cost math without prices: trace `cost` exists per turn when the
  provider reports it; otherwise counts only (never invent prices;
  models.meta.json sidecar may grow a price table later — separate
  decision).
- The ledger becoming a second source of truth: it never is —
  records + traces are; the ledger is a cached view with a stated
  refresh rule.

## Phase 0 -- shared pricing helper (detailed 2026-09-14)

Starting point (re-verified on current master; `render_price` missing everywhere):
- `sweave/web/detail_view.py:91` `render_estimate_vs_actual` SUMs every
  trace `tokens_used` event (`:102` comment, `:115` filter); the per-turn
  `tokens` section is last-wins (`:253-254` branch). No price math exists.
- `sweave/stats/ledger.py:104` `_sum_tokens_used` folds the same events
  (cost included); `context_input` is MAX-folded peak (the billed-vs-size
  incident rule). Cells built by `_cell()` (`:161`) via `build_summary`
  (`:169`); cost accumulates with the token keys (`:~240`).
- `sweave/config/manager.py:200` `get_model_meta(qualified_id)` reads the
  `models.meta.json` sidecar (`:210`); absent sidecar returns `{}` (`:203-206`:
  best-effort, never load-bearing).
- `sweave/models_sync.py:339` `build_metadata` keys meta by qualified
  `provider/model` (`:385`); `cost` carried upstream-or-serve (`:379-381`).
- Variant strip rule: `parse_model_ref`
  (`sweave/runtime/specialist_store.py:129`) -- a `+variant` suffix is valid
  only when non-empty and `/`-free (`:139-140`), split in `_split_variant`
  (`:166`). A `+` tail containing `/` stays model id.

Spec: new pure module `sweave/stats/pricing.py` (recommended -- one math
shared by `detail_view.py` + `ledger.py`, no duplication). Input: summed
`tokens_used` + qualified model id. Lookup: strip the variant per
`parse_model_ref` semantics, `get_model_meta` on the bare `provider/model`.
Rates: sidecar `cost` shape `{input, output}` per 1M; formula
`input*in_rate/1M + output*out_rate/1M`. Reasoning is a subset of output --
never additive. `cache_*` ignored v1 (marked approximate). Tiered
`context_over_200k` ignored v1 (marked approximate).
- Missing or 0/0 rates -> nulls: `free: null/unknown`, `estimated: true`,
  display "unpriced" -- never zeros.
- Provider cost wins on key-presence, not value: a present numeric `cost`
  (including 0-with-tokens) is the actual (`source: "provider"`,
  `estimated: false`); an absent key falls back to the rates estimate
  (`source: "rates"`, `estimated: true`). Absent-vs-zero stays distinct;
  mixed-turn source (`any` vs `all` provider-reported) defined at
  implementation.
- `free: true` only when explicitly free (0/0 rates) or provider cost 0
  with tokens.
- Output: `{model, rates_per_1m, estimated_cost, free, estimated, source}`.
- Fold: additive `price` key into `GET /api/delegations/{id}/detail`
  (same degrade contract: missing trace/record -> nulls, never raises) +
  a `sweave log` price line. NO retention passthrough (dropped per ruling).

## Phase 1b -- stats graphs + costs (detailed 2026-09-14)

Starting point (re-verified on current master):
- `sweave/web/routers/stats.py:37` serves `GET /api/stats/summary` via
  `build_summary` (`:13`, `:47`); compute-on-read, windowed `days 1..365`.
- `sweave-web/src/pages/Stats.tsx` (201 lines): `StatCard` (`:21`),
  `SplitTable` (`:33`), `stats-summary` query (`:89-90`), WS
  `delegation.status_changed` invalidation (`:94-95`), Cost card showing
  `t.cost.toFixed(4)` or em-dash + "provider reports no prices" (`:148-149`).
  No SVG/graphs today.
- Types: `Tokens` (`sweave-web/src/types/index.ts:276`, `cost` at `:282`),
  `StatsCell extends Tokens` (`:292`), `StatsSummary` (`:297`, totals `:297`).
- Tests: `tests/test_stats_ledger.py` (248 lines: `:44` token sums, `:65`
  peak-not-sum, `:90` error classes, `:101` day window, `:117` wall seconds,
  `:131` garbage, `:142` reader, `:212` empty endpoint, `:222` records
  endpoint).

Spec: backend ledger cells gain `estimated_cost` + `cost_source` + `unpriced`
via the shared helper (compute-on-read; degrade zeros/nulls -- unpriced
cells never render as $0). API: additive fields on `/api/stats/summary`
(same shapes, same window). UI: per-day SVG sparkline/bar (tokens + cost,
zero new deps, pure SVG+CSS) + cost column in `SplitTable` + totals
estimated-cost card with Free/unpriced states. Types: `StatsCell` /
`StatsSummary` extended. WS invalidation unchanged (`Stats.tsx:94-95` already
refetches on settle).

## Rulings (locked 2026-09-14)

1. Shared module (`sweave/stats/pricing.py`) -- one math, no duplication.
2. Explicit-free only -- `free: true` never inferred from missing data.
3. Presence-based cost-wins -- key-present numeric (incl. 0) beats rates.
4. Retention dropped -- no passthrough field; ZDR merge stays additive later.
5. Lineage: ported from doc-branch commits bf052dd + 4095ff4 onto this doc
   branch; neither commit has landed on master.
6. Graphs zero-dep SVG v1 -- no chart library.

## Phase 0/1b non-goals

No chat card pill; no live cost ticker; no tiered exact math; no
scheduler/daemon (compute on read holds); no prompt/response text
collection, ever (plan boundary).

## Phase 0/1b risks

- Engine `cost: 0` confusion -- an explicit provider 0 with tokens reads
  Free/actual; a silent/estimated 0 must never present as a bill. The
  `source` + `estimated` fields carry the distinction; UI copy must keep it.
- Midnight/retries double-count -- `created_at`-day attribution, retries
  linked by parent not merged (pinned by test; `test_stats_ledger.py:101`
  precedent).
- Never-invent-prices -- unknown model or empty sidecar degrades to
  counts-only (`test_stats_ledger.py:44` degrade precedent).

## Test files the Phase 0/1b implementation will add (not created here)

- `tests/test_stats_pricing.py` -- helper cases (free / estimated /
  tiered-approx / provider-wins incl. 0-with-tokens / mixed-source /
  missing-rates) + detail fold + `sweave log` price line.
- `tests/test_stats_costs_1b.py` -- ledger cell cost fields + summary
  endpoint additive fields + midnight/retry cost pins.
- `sweave-web/src/pages/__tests__/StatsCosts.test.tsx` -- sparkline/bar
  render, `SplitTable` cost column, totals card Free/unpriced states.
