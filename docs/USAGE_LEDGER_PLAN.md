# Usage ledger — local-first product analytics + training fuel

Status: planned (2026-09-12). User-asked: the trace-measured
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
- Cost math without prices: trace `cost` exists per turn when the
  provider reports it; otherwise counts only (never invent prices;
  models.meta.json sidecar may grow a price table later — separate
  decision).
- The ledger becoming a second source of truth: it never is —
  records + traces are; the ledger is a cached view with a stated
  refresh rule.
