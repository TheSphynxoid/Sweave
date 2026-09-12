# M2.1 — Wait-set flag + review-request (execution-ready spec)

Status: **done** (2026-09-12). All 7 steps landed; gates green
(746 pytest, 13/13 `run.py --check`, M2.1 subset 3×). Behavioral
live check (blocking defer → gated synthesis on :8100) is OPEN —
needs a server restart; the running server predates this change.
Plan of record for the execution below. Parent: `docs/M2_PLAN.md`
§4 (sketch) + `docs/PLUGGABLES_PLAN.md` §3 (locked semantics). Implements the locked wait-set + review-request
rulings; amends M1.6/M1.7 behavior — highest regression risk in the
series, so synthesis + gating tests run first.

## Rulings (user-locked 2026-09-11 — read first)

1. **`blocking` default false.** `defer` stays non-blocking unless the
   caller opts in; `false` = fire-and-forget into the Children lane.
2. **`review_request` is an embedded TypedDict on Delegation** (the
   `Manifest`/`Estimate` precedent — `sweave/runtime/delegation_store.py:76-113`),
   not a separate store or record type.
3. **Promote keeps `review_request` as history.** `POST
   /api/delegations/{id}/promote` flips `review → done` and leaves the
   review-request in place (audit trail, R6 signal).
4. **No verdict payload in M2.1** (deferred to M2.2 contract +
   conformance check). Verdicts flow through `promote` only.
5. **The `config.yaml` + `models.yaml` worktree hunks stay dirty.**
   Do NOT revert, do NOT commit — the live server depends on them;
   history cleanup happens later.

## Starting point (executor: verify before touching code)

- Delegation schema is at **v7** (`sweave/runtime/delegation_store.py:55`;
  v7 = `estimate`, migration `_migrate_v6_to_v7` at `:296-304`;
  `from_dict` migrates forward at `:197-214`; unknown fields dropped
  at `:221-223` per gotcha #12).
- **Two waits, two settled-sets (the :898 mismatch).**
  `ChatLoop._wait_for_children` (`sweave/chat/loop.py:346-391`) waits on
  ALL children (`parent_task_id ==` parent, `:365-368`) and treats
  `review` as settled (`:372`: `done/failed/review`). `JobRunner`
  `._wait_for_children` (parent gating, `sweave/runtime/job_runner.py:866-926`)
  waits on ALL children (`:891`) but settles only on `done/failed`
  (`:898`) — a child sitting in `review` wedges its parent until
  `turn_timeout`. M2.1 reconciles both waits onto the same rule (step 5).
- Success path: `job_runner.py:812-839` — `result.success` →
  `final_status = "review"` (`:813`), parent gate (`:822-825`), then
  `_transition` (`:833-839`). Failure goes straight to `failed`
  (`:814-815`, gate skipped). The review-request write lands on the
  success branch only (step 3).
- Submit paths: `POST /api/v2/tasks` (`sweave/web/routers/delegations.py:56-71`
  `TaskSubmitV2`; estimate precedent `:66-71`, plumbed at `:284-290`);
  `JobRunner.submit` (`job_runner.py:181-196`); MCP `_defer`
  (`sweave/mcp/__init__.py:232-303`; estimate passthrough precedent
  `:261` / `:273-278` / `:287-288`; success line `"queued:
  <id> (target=...)"` at `:303`). Rejected-chain rules unchanged.
- Promote seam: `POST /api/delegations/{id}/promote`
  (`routers/delegations.py:651-709`) — review-only guard at `:668-673`,
  trace + WS + bridge at `:684-707`. Step 3 keeps this endpoint's
  contract; it only stops clearing (it never cleared — there is
  nothing to clear yet) and must explicitly preserve `review_request`.
- Locked semantics: wait-set (`docs/PLUGGABLES_PLAN.md:75-79` —
  `blocking` puts the child in the synthesis join set,
  `ChatLoop._wait_for_children`) and review-request (`:80-86` — status
  `review` + request record; orchestrator resolves explicitly via
  `defer(target=reviewer)` or batched at wait-set settle; specialists
  never spawn reviewers).
- Baseline: **707 pytest** collected (2026-09-11), 13/13 `run.py --check`.

## Goal state

Every task delegation may carry `blocking: bool` (default `False`,
supplied at submit) and, once finished, an optional `review_request`
record (reviewer-role hint, diff pointer, manifest/confidence summary).
`ChatLoop` synthesis joins **only** `blocking=true` children;
fire-and-forget children land in the Children lane without gating the
turn. A `review` child counts as settled in **both** waits. The
orchestrator resolves review-requests explicitly (`defer` to a
reviewer, or batched at wait-set settle); promotion keeps the request
as history. No verdict payload, no enforcement, no UI changes.

## Steps

1. **Schema v7→v8: `blocking` + `review_request`** (~0.25 session).
   `blocking: bool = False` (orchestrator opt-in, recorded at submit);
   `review_request: ReviewRequest | None = None` — embedded TypedDict
   (`reviewer_hint: str`, `diff_ref: {worktree_path, branch, pr_url}`,
   `manifest_summary/confidence`, `requested_at`) on the
   `Manifest`/`Estimate` pattern. `_migrate_v7_to_v8` (absent →
   `False`/`None`); `from_dict`/`to_dict` round-trip; full v1→v8
   chain. Done-gate: migration-matrix tests incl. v1→v8,
   round-trip, unknown-field drop still holds.
2. **Submit-path plumbing** (~0.25 session). `POST /api/v2/tasks`
   accepts optional `blocking` (default false); MCP `defer` accepts
   optional `blocking` and passes it through (`blocking` non-bool →
   `rejected:` line, same discipline as the estimate non-dict guard
   at `mcp/__init__.py:273-278`); `JobRunner.submit` stores it.
   Rejected-chain (loop/depth/budget) rules unchanged. Done-gate:
   default-false test, true-plumbed end-to-end (defer → record),
   non-bool rejection shape.
3. **Review-request emission** (~0.25 session). On the success branch
   (`job_runner.py:812-839`): when transitioning to `review`, attach
   `review_request` (reviewer hint from the finishing agent's
   config/role; diff pointer from `worktree_path`/`branch`/`pr_url`;
   manifest intent + confidence when present) + a `review_requested`
   trace event. Failure branch (`failed`) attaches nothing.
   `promote` preserves the request (history — ruling 3; pin with a
   test, not just absence of a clear). Done-gate: success→review
   carries a populated request; failed→no request; promote keeps it;
   pre-M2.1 `review` records load with `review_request=None`.
4. **Wait-set-scoped synthesis** (~0.25 session). `ChatLoop.
   _wait_for_children` joins only children with `blocking == True`
   (the join set); `blocking=false` children are excluded from the
   gate (still listed in Children, still in the trace — fire-and-
   forget is a lane, not a void) + a `wait_set_scoped` trace event
   naming the skipped ids so the scoping is auditable. Empty join
   set returns immediately (no deadline burn on all-fire-and-forget
   turns). Done-gate: blocking-only join, mixed blocking/non-blocking
   (turn gates on the blocking child alone), all-fire-and-forget
   (immediate return), trace pins the skipped set.
5. **Parent-gate reconciliation (the :898 fix)** (~0.25 session).
   `JobRunner._wait_for_children` adopts the same rule: gate only on
   `blocking` children, and `review` counts as settled (matching the
   chat loop's `:372`). Today a successful child (which lands in
   `review` at `:813`) wedges its parent until `turn_timeout` — with
   review-requests this becomes the common case, not the edge, so
   the fix is load-bearing, not cosmetic. Done-gate: review-settles
   regression test (parent advances with a `review` child — fails
   on current code), non-blocking child never gates, timeout path
   still emits `children_settle_timeout`.
6. **Review resolution seam** (~0.25 session). No new endpoint, no
   verdict payload (ruling 4): the orchestrator resolves a
   review-request explicitly via `defer(target=reviewer)` (already
   possible — document the contract in the synthesis prompt note,
   one-authority rule stands: specialists never spawn reviewers) or
   batched when the wait-set settles (synthesis includes pending
   review-requests of join-set children). Read side: `review_request`
   rides `to_dict` (no new endpoint — the M2.0 detail-fold
   precedent); fold it into the detail projection alongside
   `estimate_vs_actual`. Done-gate: resolve-via-defer contract test
   (reviewer child links `parent_task_id` to the review delegation),
   wait-set-settle surfaces pending requests, detail includes the
   request, unknown-id degrade contract holds (200 + nulls, per the
   M2.0 amendment 6).
7. **Gates + docs** (~0.25 session). New pytest (~+25: steps 1–6
   above), full suite green 3×, `run.py --check`, live check on the
   running server (blocking defer → gated synthesis; fire-and-forget
   → immediate turn with child in Children; finish → review +
   request visible; promote → done with request kept). Update
   `DESIGN.md` §4 row, `PROJECT_STATE.md` (M2.1 progress + post-
   execution summary), `docs/GOTCHAS.md` (the :898 mismatch lesson:
   two waits, one rule — cite both sites), and this file's Status
   header planned→done. Per ruling 5 the config/models hunks stay
   dirty and uncommitted.

Total ≈ 1 session (matches the `docs/M2_PLAN.md` §1 estimate).
Run the synthesis + gating tests (steps 4–5) first — they pin the
amended M1.6/M1.7 behavior before the emission work builds on it.

## Explicit non-goals

- No verdict payload on review-requests (M2.2 contract + conformance).
- No contract-first fanout, no conformance check (M2.2).
- No auto-promote / auto-done of `review` (promotion stays explicit).
- No per-specialist tool policy (M2.3), no golden sets / evals (M2.4),
  no calibration (M2.5).
- No `blocking` on chat-turn delegations (task delegations only —
  the M2.0 chat-estimate non-goal, same boundary).
- No UI changes (Children lane renders existing shapes; review-request
  display binds later on the `to_dict` + detail contract).
- No R4.4 dependency.

## Risks

- **Highest regression risk in the series** (M2_PLAN §4): steps 4–5
  amend M1.6 parent gating + M1.7 synthesis. Mitigated by running
  those tests first and keeping both waits' settled-rule in one
  named constant/helper both call sites share (no second drift).
- **The :898 fix can mask stuck children**: treating `review` as
  settled means a never-promoted child no longer blocks — by design
  (promotion is explicit and may lag), but a parent that needed the
  reviewed output proceeds without it. Mitigated by the
  `review_requested` / `wait_set_scoped` trace events (the skip is
  always auditable) — never silently absorbed.
- **Join-set starvation**: all-blocking turns with one wedged child
  still burn the full `turn_timeout` (unchanged semantics — bounded
  wait, then synthesize on what's terminal). Fire-and-forget is the
  escape hatch; document it in the orchestrator note (step 6).
- **`review_request` history growth**: one small embedded dict per
  finished delegation (bounded, no new store, no retention change).

## Execution summary (2026-09-12)

Commits (each step its own; numbering continues the session's
`M2.1 step N` sequence): `cd18fcc` (char tests), `1d60ae9`
(schema v7→v8), `7d83506` (submit plumbing), `07331a5`
(producer), `c498d09` (waits), `fd4aaa3` (resolution).

Amendments (with justification):
- Plan steps 4 + 5 landed as ONE commit (`c498d09`): the shared
  `JOIN_SETTLED_STATUSES` / `in_join_set` / `is_join_settled`
  helpers make the two waits atomic — splitting them would leave
  the tree in a half-migrated settled rule. Done-gates for both
  steps hold in `tests/test_m2_1_waitset.py`.
- `c498d09` also fixed three stale M2.0 version pins (`== 7` →
  `== 8`) missed by the schema commit — same-rule hygiene, no
  behavior change.
- Session `Sweave-20260911-213619-096e65` timed out at the 300s
  chat-transport stall after writing files (transport timeout ≠
  work rollback); this session resumed from its dirty tree
  (`loop.py` wait-set + store helpers + `docs/M2_PLAN.md` note)
  and completed the plan. Ruling 5 held throughout:
  `config.yaml` / `models.yaml` hunks untouched and uncommitted.

## Test plan

~+25 pytest on top of the 707 baseline: schema (v1→v8 chain,
round-trip, defaults, unknown-field drop); submit (blocking
default-false, true-plumbed via API + via MCP defer, non-bool
rejection); emission (success attaches, failed attaches nothing,
promote preserves, legacy `review` loads `None`); waits
(review-settles regression, non-blocking never gates in either
wait, mixed join-set, all-fire-and-forget immediate, timeout event
shape, skipped-set trace); resolution (defer-to-reviewer linkage,
settle surfaces pending, detail fold, unknown-id degrade). Gates:
step tests + `run.py --check` + 3× suite green + the step-7 live
check. If a gate fails, fix before moving on (established method).
