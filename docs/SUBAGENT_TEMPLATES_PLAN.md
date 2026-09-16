# Subagent templates — plan of record

Status: **specified** (2026-09-16, this session — execution NOT
started). The tier ruling below is PROPOSED (recommended);
execution waits on the user lock. Predecessor reading: DESIGN.md
§2 (concepts) + §6 (M2/review rows), `docs/REVIEW_HARDENING_PLAN.md`
§0 ruling 3 + §5 sequenced items, `docs/SUPERVISOR_PLAN.md` (done),
`docs/M2_1_PLAN.md`, `sweave/agents/loader.py`,
`sweave/runtime/subagent_store.py`, `sweave/runtime/agent_permission.py`.

## 0. Motivating incident (2026-09-16, verified from trace)

Chat turn `chat-12c8f333ee16` deferred backend work (blocking,
joined, settled into `review` normally), then its *synthesis* turn
deferred reviewer `5b0545533715` at 01:45:37 and finished at
01:45:43. `ChatLoop` waits once — after the first turn
(`sweave/chat/loop.py:1898`) — so synthesis-turn defers escape the
join by construction: the turn reads `done` while a
`blocking: true` reviewer runs on. Three findings, each a spec
item below:

1. **No exact queue key.** A reviewer spawned by plain `defer`
   carries no structured link to *what* it reviews — only task
   text mentioning a commit SHA. "Already under review" is not
   machine-readable, so a second defer can't be deduped exactly.
2. **Standing state with no conversational payoff.** The reviewer
   is a persistent Specialist (seed record, model override,
   durable session, `Delegation` row, promotion lifecycle) but
   nobody ever chats with it — every review is fire-per-task.
   Durable context across reviews is bias risk, not value (live
   exhibit class: the stale `+max` model-override lane).
3. **MCP path is not the cost.** Four fixed tool schemas are a
   few hundred tokens against 70K+ turns. Templates change the
   receiving end, not the call — context savings are not the
   motive; lifecycle hygiene is.

## 1. Rulings (PROPOSED 2026-09-16 — needs user lock before execution)

1. **Template persists, runs are ephemeral.** The seed file
   (prompt + tool policy + default model + worktree policy) is
   the template, like opencode agent definitions. Each review is
   a fresh session with a retired tree; judgment lands on the
   *reviewed* delegation via the existing `POST .../verdict`.
   The 2026-08-29 "specialists persist" ruling stands for
   implementers; the reviewer moves tier.
2. **MCP `defer` stays the only spawn path.**
   `agent_permission.py`'s native-`task` deny stands on both
   roles. A native engine task tool would fork spawning past
   the DelegationManager gates for ~zero token savings —
   explicitly rejected.
3. **Verdict/fix stack untouched.** M2.2 verdict, fix rounds,
   promotion, review bundle, traces all live on the reviewed
   side already; templates consume them, never move them.
4. **Template = prompt + tools + model default + worktree
   policy.** No per-template harness, no lifecycle hooks, no new
   MCP slots (hardening-plan constraint stands).
5. **Visibility before migration.** Ephemeral runs get their
   Children-lane projection BEFORE the reviewer moves tier —
   reviews must never go dark.

## 2. Template shape

- Definition: today's seed format (`name`, `description`,
  `prompt` with `{{var}}` render, `tools.builtins`,
  executor model/harness) plus a tier marker and run-scoped
  vars (`{{review_of}}`, `{{worktree_path}}`,
  `{{workspace}}`). Location open: `tier:` field in
  `sweave/agents/*/` vs new `sweave/subagents/*/` dir.
- The structured `review_of` (target delegation id) rides the
  defer/submit path — this is the exact queue key from §0.1
  and the REVIEW_HARDENING step-5 "review-target resolution"
  item, delivered as a side effect.
- Discovery: template entries served alongside (clearly badged
  apart from) `list_specialists`; descriptions stay one line;
  opencode's `hidden` pattern for internal-only templates.
  Specialist-vs-template name collisions need a precedence rule
  (open — proposed: specialist wins + warn).

## 3. Run lifecycle

Fresh engine session by default (fresh-eyes hygiene); explicit
resume only (opencode `task_id` precedent). Worktree:
inherit-from-target or none, never owned, always retired
(review keeps its tree today — that ends). No model-override
persistence, no durable session, no promotion lifecycle, no
`needs_attention` on the run itself. Output transcribed onto
the reviewed delegation (human `POST .../verdict` today, as
now). No further defer from inside a run (one-authority rule +
"prove, don't fix" already require this).

## 4. Queue guard (the §0.1 item)

Reject-first (recommended): defer while a live run exists for
`(template, review_of)` → 409 → `rejected: <template> already
running for <target> (<run_id>)`, reusing the fix-round
double-spawn precedent. Enforced on the submit path (router,
store access — NOT `DelegationManager.validate`, which sees
only the parent). The orchestrator closes the turn citing the
running id; results land in the Children lane. True queue
(hold-and-spawn-fresh) deferred: human re-defers today.

## 5. Migration order

1. Template machinery (loader tier marker, render vars,
   discovery badge, run record + Children projection).
2. Reviewer as first template (carries ruling 3's generic
   review permission — user-added auditors ride free).
3. Retire the reviewer Specialist binding (seed stays until
   cutover; Agents card moves to template source).

## 6. Non-goals

No verdict/promotion changes; no supervisor/timer changes
(supervisor DONE — `docs/SUPERVISOR_PLAN.md`); no gotcha-store
or transcript-search endpoints; no opencode-side changes; no
plugin framework (ruling 4 bounds the shape); no UI beyond the
run projection.

## 7. Risks

- Seed-text shape changes touch every consumer of the loader
  (resolver, MCP list, Agents UI) — per-turn render tests +
  loader checks pin it.
- `review_of` is a schema touch on the submit path — additive
  optional field, never required, old records read as unlinked.
- Parallel threads share DESIGN/GOTCHAS/PROJECT_STATE —
  one concern per commit, additive paths only.

## 8. Gates

Hermetic: loader tier tests, render-var tests, queue-guard
unit tests (live-run hit/miss/multi-target), precedence test.
Live: template review end-to-end on an auth'd window
(defer → run → verdict transcribed → tree retired →
Children projection). Full pytest green 3× + `run.py --check`
per step.

## 9. Open decisions (for the detailing round)

1. Tier ruling lock (option 3 recommended; §1).
2. Template location: `tier:` field vs `sweave/subagents/`.
3. Queue behavior: reject-first vs true queue (§4).
4. Name-collision precedence (proposed: specialist wins + warn).
5. Calibration (M2.4) before or after migration (ruling 2
   says reviewer quality is the load-bearing gate).
