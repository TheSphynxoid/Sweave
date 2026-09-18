# Review as a feature — plan of record

Status: planned (2026-09-17); execution order locked 2026-09-18:
step 0 (backstop live-verify) → step 1 → step 3 → step 2-human.
Review today is not a feature (a status + a promote button);
this plan makes it one. Scope locked with user: Verdict UI +
Request flow + pre-promote Diff review. Fix-round tracking stays
with chat/specialist (out of scope here).

Thread: verdicts (`decision/comments/confidence/reviewer/
gotcha_hits/fix_assignee`) are write-only today — submitted via
`POST …/verdict`, served by the detail fold, rendered nowhere
(zero frontend references to `verdict`/`review_request`/
`review_bundle`). `review_request` is system-built at settle
(`build_review_request` + reviewer hint); no human-driven request
flow exists. The diff bundle is captured but never shown before
Mark done.

## Rulings (user-locked 2026-09-17 — read first)

- **Scope = three slices, nothing else.** Verdict display,
  request flow, diff-before-done. Fix-round chains, lanes, and
  synthesis behavior are other features' business.
- **Verdict stays advisory.** Recording never changes status,
  never clears attention, never promotes (M2.2 contract holds).
  Display only; no new write paths except the request flow.
- **Promote stays human-only.** The diff gate informs, never
  auto-approves; no auto-promote on approve verdict.

## 0. Starting point (re-verified 2026-09-17 against code)

- Backend (done, untouched by this plan): `review` status on
  implementation success; `POST …/promote` (409 unless review);
  `POST …/verdict` (`decision: approve|request_changes`,
  comments, confidence, reviewer, gotcha_hits,
  output_claims_checked, fix_assignee; advisory only);
  `POST …/fix-round` (supervised path); `review_fix_mode`
  direct/supervised + `review_fix_max_rounds`; `review_request`
  auto-built with reviewer hint; `review_bundle` diff pointer;
  all folded into `GET …/detail` (`detail_view.py`).
- Frontend (the gap): Children `LiveTree` review rows carry
  Mark done + Answer gating + escalation preview only. No
  verdict badge/section, no findings list, no request affordance,
  no diff surface. `DetailView` has Transcript/Tools/Tokens tabs;
  no review tab.

## 1. Goal state

- Every `review` row shows its verdict state at a glance
  (unreviewed / approve by X / request_changes by X with
  finding count), and the detail view carries a Review tab:
  decision + reviewer + confidence + comments + gotcha hits +
  linked fix rounds.
- A human can request review work: ask a reviewer (specialist
  or self-note) for a verdict on a `review` row, and assign the
  fix worker (`fix_assignee`) — through the existing verdict /
  fix-round endpoints, surfaced, not reinvented.
- Mark done happens with the diff in view: the review row /
  detail shows the bundle diff inline (collapsed, capped),
  so promotion is eyes-on, never blind.

## 2. Steps

### Step 0 — Backstop live-verify (ops, first)

1. Restart the server (picks up `12656bb`), run a review-owed
   turn, and check the reviewer's `parent_task_id` == the review
   TARGET's id (not the chat turn). Evidence session
   `Sweave-20260918-002816-34a49c` ran pre-restart: reviewer
   `4e6868a49400` chat-parented, no verdict — the old pattern.
2. Done-gate: one probe turn with correct parentage; then steps
   1/3 build on real data.

### Step 1 — Verdict display (backend: none; UI only)

1. `LiveTree` review rows: verdict badge (Approve green /
   Request-changes amber / Unreviewed grey) + reviewer name +
   finding count from the detail fold's verdict (already
   served; add to the row's query or reuse DetailView fetch).
2. `DetailView` Review tab: full verdict (decision, reviewer,
   confidence, comments rendered markdown, gotcha hits as
   chips, fix_assignee + fix-round count linking the child).
   Absent verdict degrades to today's row (no empty tab).
3. Done-gate: vitest (badge per decision, absent-verdict
   degrade, findings count), `npm run build` green.

### Step 2 — Request flow (thin backend, UI)

> Amendment 2026-09-17 (system side landed first): the turn-end
> backstop (`ChatLoop._backstop_uncovered_reviews`) now dispatches
> reviewer coverage itself — uncovered `review_request`s get a
> reviewer delegation parented to the review target, validated
> through the chain rules, no model follow-up needed. What remains
> in this step is the HUMAN side below (affordance + assignee);
> the dispatch half is done and pinned by
> `tests/test_review_backstop.py`.
>
> Amendment 2026-09-18 (fix lineage landed): fix children cut
> their tree FROM the reviewed branch (`_fix_base_branch` — the
> fixer's tree contains the code under review; unverifiable base
> falls back to HEAD with a trace event), and the fix brief
> carries the branch/worktree pointer. Pinned by
> `tests/test_fix_lineage.py` (incl. a real-git proof). The fix
> endpoint / verdict contract is unchanged.

1. Contract check first: `review_request` auto-build covers
   the system side; the human side needs "ask X for a verdict"
   = verdict endpoint is reviewer-agnostic (anyone POSTs), so
   the flow is UI + one spawn path: requesting from a
   *specialist* reviewer spawns a reviewer delegation (reuse
   the fix-round spawn shape, kind review); requesting from
   *self* is a note (no endpoint — out, notes don't exist).
   Lock this semantic before building.
2. UI: review row "Request review" affordance (reviewer pick:
   reviewer-specialist / custom agent list) → spawns + links;
   `fix_assignee` editable on the verdict (PATCH or reuse
   verdict POST? lock in step).
3. Done-gate: pytest (spawn links parent, assignee persists),
   `run.py --check`, vitest (affordance states).

### Step 3 — Diff-before-done (UI binding)

1. Review row expander + detail Review tab embed the bundle
   diff (reuse `EditTool` diff card / `ToolDetailMeta`
   patterns from TOOL_CARDS step 2; caps hold).
2. Mark done stays one click — the gate is presence of the
   diff, not a checkbox (locked: inform, never block).
3. Done-gate: vitest (diff renders capped, absent-bundle
   degrade), Playwright only if the repo gate demands.

## 3. Explicit non-goals

- No fix-round chain UI (chat/specialist feature).
- No auto-promote, no verdict-gated promotion, no status
  changes from display (advisory contract holds).
- No new reviewer agent semantics (reviewer-specialist
  charter untouched).
- No lanes/synthesis/turn changes.

## 4. Risks

- Step 2 semantic drift (request = spawn vs note) → locked
  up front, test first.
- Detail-fold weight (verdict + bundle on every row fetch)
  → rows fetch verdict lazily (detail/open only), never in
  the tree list query.
- Duplicating TOOL_CARDS diff renderers → reuse, never fork
  (`EditTool`, `ToolDetailMeta`).
