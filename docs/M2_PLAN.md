# M2 — Backend capabilities (ordered series)

Status: fast-track + M2.0 DONE (2026-09-11); M2.1 planned
(see `docs/M2_1_PLAN.md`); M2.2+ still sketched (§4)
(see §6 execution record). Plan of record for the next execution
session(s). Thread: M2-started (see PROJECT_STATE "Threads
(2026-09-11)"); R4 continues as the sidelined parallel thread.
Companion: `docs/PLUGGABLES_PLAN.md` (taxonomy + locked
mechanics + explored directions). M2 implements the backend half;
UI binds later on API contracts.

## 0. R4 verdict: NOT finished, deferred as a parallel track

Verified 2026-09-11 against docs + code:

- R4 wave 1 ✅, R4.0 ✅, R4.1 ✅. TRACKING Phase A ✅.
- R4.2: steps 0/1/2a/2-pre shipped; remainder (2b/2c/3) waits on the
  USER's visual sign-off of `/chat` — gated on the user regardless.
- R4.3: table row in `docs/R4_PLAN.md:53` but NO detailed plan file
  (`docs/R4_3_PLAN.md` does not exist). Unplanned.
- R4.4: re-cut planned (`docs/R4_4_PLAN.md`, 6 steps, ~4.5 sessions),
  mostly backend (contract fix, local-first backend, pane conformance,
  hosted opt-in, secrets, docs). Memory backend is doubly broken
  (JSON-body 422s + no usable default backend).

Ruling locked 2026-09-11: M2 proceeds NOW on the backend;
R4-remainder runs as a parallel UI track whenever the user drives it
(UI has been user-derived since the R4.4 intervention — parallel
tracks fit established practice). Discipline holding them together:
every M2 step ships API contracts + pytest, so UI binds later without
rework (the M1.9 `detail_view.py` precedent). Exception: the R4.4
local memory backend returns when group-memory/lore work starts
(M3 at earliest) — nothing in M2 needs it.

## 1. Series order (prerequisite-sorted, not excitement-sorted)

| Phase | What | ~sessions | Unlocks |
|---|---|---|---|
| M2.0 | Estimation records (record-only) | 0.75 | planner, velocity, denser rewards |
| M2.1 | Wait-set flag + review-request | 1 | reunion join sets, R2 cross-review input |
| M2.2 | Contract record + conformance check | 0.75 | contract-first fanout |
| M2.3 | Per-specialist tool policy | 1.5 | reviewer guarantees, MCP/user-server governance |
| M2.4 | Golden-set v0 + eval runner | 1 | harness CI, training distribution |
| M2.5 | Dogfood minimal (calibration on records) | 1 | R6 heads, planner data |

Beyond M2 (explicitly NOT in this series): query planner (needs
calibration volume from M2.0), group memory (needs R4.4 backend),
runtime-driven reunion v1 (needs M2.1+M2.2), training env API +
trajectory export, audit/provenance export, postmortems, replay
debugger, skills-with-tests, topology gen, onboard metric.

## 2. Fast-track (pre-M2): user default out of models.yaml

Accepted defect 2026-09-11 (user proposal, verified against code).
Three writers share one file today: `set_default_model` persists the
user's selection INTO models.yaml (`config/manager.py:265-320` via
`_persist_models`, which rewrites providers+default at `:343-358`);
`sync_registry` reads `old_default` from the same file and rewrites it
(`models_sync.py:520,565`); any stale read (fresh checkout, parallel
session, competing writer) silently replaces the user's selection —
the stray `default: opencode/muse-spark-...` M seen 2026-09-11 is the
live exhibit. Generated artifact + user state in one file is the bug.

### Starting point (executor: verify before touching code)

- `SweaveConfig.models.default` exists on the schema and already wins
  on the read path (`manager.py:228-230` prefers it; `:90` merges it).
- Config write precedent: `SweaveConfig.to_yaml(path)` at
  `config/schemas.py:158`; routing persist at `manager.py:404`.
  Confirm no manager-level config saver exists before adding the call
  (don't invent a second write path).
- `load()` reads models.yaml `default` at `manager.py:61-66`, customs
  overlay at `:75-77`, merge at `:88-90`.
- `write_registry(path, providers, default)` at
  `models_sync.py:445-459`; callers: `sync_registry` (`:565`) plus any
  CLI/test callers (grep — signature change ripples).
- `POST /api/models/regenerate` (`web/routers/config.py:112`) and
  `sweave models sync` both funnel into `sync_registry` (verify).

### Steps

1. New home: `set_default_model` writes the bare default to
   `config.yaml` (via the `to_yaml` path) and never touches
   models.yaml. Validation logic unchanged (registry still the
   allowlist for selectability).
2. Precedence: config.yaml default wins; models.yaml `default`
   becomes a legacy fallback read only when config is unset;
   customs overlay behavior unchanged. `get_default_model` documents
   the order.
3. Migration (once, idempotent): on load, if config default is unset
   and a legacy models.yaml default exists and is selectable, adopt
   it into config.yaml (and leave the file key in place but
   henceforth ignored — no destructive rewrite of user data).
4. Generator purity: `write_registry` drops the `default` param;
   `sync_registry` stops reading `old_default` (the `default_kept`
   report goes with it); sync output is providers-only.
5. Retire `_persist_models`' registry rewrite (second clobber
   vector): after steps 1–4 nothing but sync writes models.yaml.
   If callers remain, repoint or delete with justification.
6. Gates + docs: pytest (set→sync→survives round-trip; legacy
   adoption iff-config-unset; sync output contains no `default` key;
   parallel-writer scenario: sync after external default change keeps
   config value), `run.py --check`, suite 3×, live check (set default
   via API → regenerate → default survives + turn routes on it);
   PROJECT_STATE entry; GOTCHAS (three-writers lesson).

### Explicit non-goals

- No registry format change beyond dropping the `default` key
  (legacy read stays for adoption).
- No UI changes (Settings keeps calling the same endpoint).
- No R4.4 dependency.

### Risks

- In-flight server holding a stale in-memory default across the
  migration: mitigated by the `_sync_reload` contract (same as today).
- Parallel sessions writing config.yaml concurrently: check for an
  atomic-write helper on that path; if none, note it (don't build
  locking in this half-step — file it).

## 3. M2.0 — Estimation records (execution-ready spec)

### Starting point (executor: verify before touching code)

- Delegation schema is at v6 (`sweave/runtime/delegation_store.py:44`;
  v5 = `needs_attention`, v6 migration at `:171` — confirm what v6
  added; `from_dict` migrates forward per gotcha #12).
- Token estimation exists (`estimate_tokens` in
  `sweave/runtime/delegation_manager.py:88` — tiktoken, coordination
  use today).
- Actuals exist without new collection: per-turn `tokens_used` trace
  events (M1.9) + `created_at`/`completed_at` wall time
  (`delegation_store.py:103-106`).
- Submission paths: `POST /api/v2/tasks` (+ MCP `defer` posts there
  with `parent_task_id`); chat turns via `ChatLoop` (M2.0 covers task
  delegations; chat-turn estimates are a non-goal).
- Projection precedent: `sweave/web/detail_view.py` + `sweave log`
  (read-side joins over store + traces, zero new write contracts).

### Goal state

Every task delegation may carry `estimate: {tokens, seconds} | None`
(supplied at submit, nullable, no behavior change); a read projection
returns estimate-vs-actual per delegation (actual tokens summed from
trace `tokens_used`, actual seconds from created→completed). Record
only: no planner, no UI, no enforcement, no calibration.

### Steps

1. Schema v6→v7: nullable `estimate` field + `_migrate_v6_to_v7`
   (absent → None). `from_dict`/`to_dict` round-trip; v7 records on
   disk; all prior migrations keep passing (gotcha: Delegation &
   Session schema discipline).
2. Submit-path plumbing: `POST /api/v2/tasks` accepts optional
   `estimate`; MCP `defer` accepts optional `estimate` and passes it
   through (`blocking` is M2.1 — out of scope here); rejected-chain
   rules unchanged.
3. Actuals projection: `GET /api/delegations/{id}/estimate` (or fold
   into the detail projection — executor decides with justification;
   detail-view pattern preferred over a new endpoint if it stays
   clean) joining store + trace; missing-trace degrades to
   actuals-null, never 500.
4. Gates + docs: pytest (migration matrix incl. v1→v7 chain,
   round-trip, projection with/without trace, 422-shape if new
   endpoint), `run.py --check`, full suite green 3×; DESIGN §4 row;
   PROJECT_STATE entry; GOTCHAS if the schema bites.

### Explicit non-goals

- No query planner, no model selection, no budget enforcement.
- No estimate quality requirements (any caller-supplied numbers
  accepted; calibration is M2.5).
- No chat-turn estimates, no UI, no R4.4 dependency.

### Risks

- Schema-bump fatigue (v7 one milestone after v6): mitigated by the
  migration-chain tests, which are the actual gate.
- Trace absence for pre-M2.0 delegations: projection degrades, and
  the degraded shape is pinned by test.
- Estimate gaming later (callers low-balling to dodge future caps):
  noted, not solved — M2.0 records, nothing enforces.

## 4. Later phases (sketched; each gets its own execution-ready
section before it runs — a re-scope against what M2.0 actually built)

- **M2.1** implements the locked wait-set + review-request semantics
  (`docs/PLUGGABLES_PLAN.md` §3): `blocking` flag on `defer`,
  wait-set-scoped synthesis, review-request record, promote seam.
  Amends M1.6/M1.7 behavior — highest regression risk in the series;
  synthesis + gating tests run first.
- **M2.2**: contract record type on the parent delegation + a
  conformance check at review (boolean + diff refs, reviewer-visible).
  Seed of contract-first fanout; no auto-blocking on mismatch yet.
- **M2.3**: Specialist schema v2 tool policy + locked-set enforcement
  (task/question denies, specialist orchestration denies, escalate
  always-allowed — API 409s) + opencode per-specialist agent render
  (sidecar ownership per agent, stale sweep, `body["agent"]` probe) +
   custom-engine `tools[]/permission_map` passthrough. Proposed
   defaults (default-off new servers, locked reviewer, allow/deny only)
   lock at the M2.3 detailing round per the planning method (2026-09-11:
   agreed — phase detail surfaces its own rulings in due time).
- **M2.4**: golden-task format + one eval runner script + a first
  golden set drawn from Sweave's own repo tasks. Seeds harness CI
  and the training distribution. Curation judgment per task is the
  slow part — cap the first set small (5–10).
- **M2.5**: fit calibration on accumulated records (even heuristic
  per-specialist estimate correction counts) + a calibration report
  endpoint. Bridge into R6; no neural training in M2.

## 5. Gates for the series

Per-step: the step's pytest + `run.py --check` + 3× suite green +
live check where the plan calls for it (established execution
method). Per-series: every step leaves API contracts UI can bind
(R4-parallel discipline); PROJECT_STATE + DESIGN §4 updated per
step; no step reshapes an earlier step's records without a migration
and a justification citing this file.

## 6. Execution record: fast-track + M2.0 (done 2026-09-11)

M2.1 detailing (2026-09-11): execution-ready spec at
`docs/M2_1_PLAN.md` (7 steps, ~1 session, 5 user-locked rulings);
no code, no commits — detailing only.

Commits: `9e701a7` (fast-track steps 1–3), `ccedbfd` (steps 4–5),
`2cd150e` (M2.0 steps 1–3). Gates: 707 pytest (674 + 12 fast-track
+ 21 M2.0), 13/13 `run.py --check`, live checks on :8100 per the
plan gates (set→regenerate→survives, `/api/route` on the new
default, detail projection on a real pre-M2.0 delegation).

### Amendments (one block; executor-justified per the method)

1. **Starting-point corrections (no redesign).** (a) §2 claimed
   `SweaveConfig.models.default` "already wins on the read path
   (`manager.py:228-230`, `:90`)" — inverted: `:228-230` read the
   models.yaml-derived value and `:88-90` OVERWROTE the config value
   with it; config.yaml had no `default` key at all. Step 2's
   precedence is a behavior change, implemented as specified.
   (b) §2 claimed regenerate + `sweave models sync` "both funnel
   into `sync_registry`" — wrong: the endpoint shelled out to
   `scripts/generate_models.py` WITHOUT `--write`, so it never
   wrote the file (legacy npx generator). Only the CLI called
   `sync_registry`.
2. **Steps 1–3 shipped as one commit** (single intertwined hunk in
   `manager.py::load/set_default_model`; each behavior separately
   pytest-pinned). Steps 4–5 shipped as one commit with the
   regenerate repoint (same generator hunk).
3. **`to_yaml` NOT used for the config write** (plan suggested "via
   the `to_yaml` path"): it dumps the MERGED config, which would
   have baked providers + routing into config.yaml. The surgical
   `_set_models_default_line` + `atomic_write_text_sync`
   (`runtime/locking.py`) path preserves comments byte-for-byte.
4. **Migration guard: never adopt FROM customs.** §2 step 3 said
   "a legacy models.yaml default"; made explicit: adoption fires
   iff config-unset AND no customs default AND legacy selectable —
   the live customs layer stays dynamic (freezing it would shadow
   later customs edits under the locked config-wins precedence).
5. **User rulings (execution Q&A 2026-09-11):** scope =
   fast-track + M2.0 (M2.1+ needs its own execution-ready section
   first, per §4); regenerate REPOINTED to `sync_registry`
   (in-process, plain-`def` endpoint so the blocking fetch rides
   the threadpool); projection FOLDED into the detail view (no new
   endpoint); the working tree's stray default ADOPTED via
   migration (it is the post-execution config.yaml value).
6. **Unknown-id detail keeps the 200-degrade contract** (not a
   404): the estimate join degrades to nulls like a missing trace.

### Execution summary

Fast-track: `set_default_model` → config.yaml only (validation
unchanged); precedence config > customs > legacy; one-time
idempotent adoption; providers-only `write_registry`;
`_persist_models` deleted; regenerate in-process. M2.0: schema v7
+ `Estimate` TypedDict + full v1→v7 chain; submit + defer
plumbing (lenient shape, strict non-negatives); `estimate_vs_
actual` in detail + `sweave log`. Non-goals held: no UI changes
(Settings calls the same endpoint), no registry format change
beyond dropping `default`, no R4.4 dependency, no enforcement/
calibration/chat estimates. Gotchas landed: three-writer rule +
`safe_dump`-scalar `...` splice (both in `docs/GOTCHAS.md`,
Paths & config). Live registry churn from the gate's regenerate
(13+/3-) was restored byte-identical from backup after
verification; server left running on the new code with state as
found.
