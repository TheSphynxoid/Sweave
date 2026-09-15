# Chat timeline — plan of record (2026-09-14)

Status: in execution (delegation 50cf94db666e). Companion to
`docs/SPECIALIST_VIEW_PLAN.md` (still planned/unexecuted — timeline
specialist nodes degrade to today's `TurnDelegations` cards until the
live block + record-side subchat contracts land).

## Rulings (user-locked 2026-09-14, all YES — proceed)

1. **Option A now, enrichment later.** ChatGPT-style pager + unified
   turn-timeline shell ship now; specialist enrichment (live block +
   record-side subchat per `docs/SPECIALIST_VIEW_PLAN.md` steps 1–2)
   binds when those contracts land. Timeline nodes degrade to today's
   `TurnDelegations` card data until then.
2. **Children scope per version.** Flipping to v1 shows v1's
   specialists (join on that attempt's `chat-*` delegation id), never
   the tip's — attribution must not lie.
3. **Collapsed by default.** Timeline is a one-liner
   (`v2 · N tasks · M running · Q questions`) mounted under the
   assistant bubble; Questions + Delegations lanes live INSIDE it.
4. **Revision-preserving rerun (option a).** Edit APPENDS a new user
   message with fork linkage instead of mutating `target.content` in
   place (`sweave/chat/loop.py::rerun_turn` currently mutates + flags
   tail superseded; `tests/test_chat_rerun.py` pins mutate-in-place —
   updated this plan). Original prompt text survives so the pager has
   something to flip between. No-rotation invariant holds (history
   rewrite in place via revert, binding always kept).
5. **Read-only except Abort + Answer/Skip.** M1.7 funnel rule +
   view-plan ruling 3 hold — no second composer, no promote/merge
   from the pane.

## Starting point (re-verified 2026-09-14; verify again before touching)

- `sweave/chat/loop.py::rerun_turn` (~L747-877): mutate-in-place +
  supersede tail + history rewrite; `_run_turn_body` reuses
  `existing_user_msg` without re-emit; `_rewrite_superseded_history`
  collects traced prompt ids after `from_index`.
- `sweave/chat/transcript.py:607-613`: already skips superseded
  (active version keeps defining LLM context — view-only change).
- `sweave-web/src/lib/chat/runtime.ts`: `applyRerun` (~L679-704:
  flags tail superseded + swaps content) + `projectEntry`/
  `projectThread` linear list (~L340-395) + `isSuperseded`.
- `Thread.tsx`: `SupersededBlock` stacked-dimmed (~L624-660),
  `RoundBlock`, `SegmentedBody`/`Segments` (interleave fidelity),
  `AssistantActionBar` retry with duplicate-work confirm, `UserMessage`
  edit pencil; `TurnDelegations.tsx` (`parent_task_id` join,
  WS-pulsed) + `TurnQuestions.tsx` (blocking card).
- `docs/SPECIALIST_VIEW_PLAN.md` still planned/unexecuted (steps 0–5)
  — degrade gracefully.

## Phase 1 — Backend revision-preserving rerun

Edit APPENDS (never mutates): flag `messages[idx:]` superseded
(including the original target — it leaves the live thread but stays
on record), append a new user message
(`metadata: {fork_from: from_message_id, revision: True}`) at the end,
emit `message.added` for it (the body must NOT re-emit — it reuses
`existing_user_msg`), run the turn against the new row. Retry
(same/omitted text) keeps the old path (target stays live, no
duplicate row). `_rewrite_superseded_history(from_index=idx)` still
collects the superseded tail's prompt ids; binding always kept;
child delegations untouched; transcript exclusion unchanged; the
`rerun` trace event carries `edited + revision info`
(`fork_from`, `new_message_id`, `revision`). Route docstring updated
(no more "rotates the binding"). `tests/test_chat_rerun.py` updated:
original preserved, retry same-text path, edit append path,
non-user/unknown rejects, children untouched.

## Phase 2 — Frontend grouping + pager

`runtime.ts` gains pure helpers: `groupTurnVersions(entries)` (each
user message opens a version group; `fork_from` links revisions of
one turn; assistants attach to the preceding user group), per-version
`delegationIds` (each attempt = own `chat-*` id; children join per
version via that attempt's id), `timelineSummary` (tasks/running/
questions counts), pager index helpers. `applyRerun` goes
append-optimistic for edits (append optimistic user entry with
`fork_from`, flag target+tail superseded; retry path unchanged).
Pager `< n/m >` under the user bubble (edits flip prompt + answers +
specialists) and under the assistant bubble (retry flips answers
only); tooltips keep specialist-task count + duplicate-work confirm.
`projectThread` stays the flat projection (superseded still collapse
via `SupersededBlock`); grouping is a second projection the timeline
shell reads.

## Phase 3 — Timeline shell

Collapsed one-liner under the assistant bubble
(`v2 · N tasks · M running · Q questions`); expanded = version rows +
round narration (`RoundBlock`) + thinking/segments riding with their
version + specialist nodes (today's card data via `TurnDelegations`
scoped to the version's delegation id; live/transcript enrich later)
+ question/permission nodes (existing `TurnQuestions` answer/skip
paths reused verbatim). Questions + Delegations lanes move INSIDE the
timeline (mounted per version, not loose under the bubble). Children
tab stays the full cross-version audit log; no deletion anywhere.
Read-only except Abort + Answer/Skip — no second composer, no
promote/merge from the pane.

## Phase 4 — Gates + docs

`pytest` + `vitest` + `npm run build` + `run.py --check` +
headless-Edge screenshot probe of pager + timeline (test-before-fix:
write the failing probe first, watch it fail, then fix). Docs:
`DESIGN.md` §4 (new row: chat branching + turn timeline),
`PROJECT_STATE.md` (rulings + plan location + post-execution
summary), `docs/GOTCHAS.md` (linear→grouped projection rule;
append-not-mutate rerun rule), plan Status → done with Execution
summary. Each phase its own commit (`Chat-timeline step N: ...`).
Never merge PRs / never `git merge`.

## Explicit non-goals

No second composer/input funnel; no fork-out sidebar/named branches;
no promote/merge from timeline; no fetch-side engine-truth
transcript; no LLM-context change beyond the active-version rule; no
R4.4 dependency.

## Execution summary

(post-execution; filled per phase)
