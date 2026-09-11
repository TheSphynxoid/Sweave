# Tracking Plan — plan board / todos / lightweight tickets / schedule

Status: validated (2026-09-11). Rulings: new `/plan` Sidebar tab; schedule =
due dates + reminders, visual-only (scheduled runs = deferred future task
page); this session = Phase A read-only. Covers the plan/bug/schedule/todo ask:
a TODO/planning section in Sweave, engine + visual, Kanban + table +
bugs. Rule for this milestone (user-locked 2026-09-11): the plan does
not fit the implementation later — every change ships with a
justification citing this file's phase and the reality evidence below.

## 1. Reality evidence (measured 2026-09-11, not assumed)

- T1 — `todo` tool parts in `~/.sweave/traces/*.jsonl`: **0 across 6,396
  files / 35,446 events** (full-corpus scan). `bash`: 3 parts in 1 file.
  Verdict: projecting a todo view from traces ships an EMPTY page.
  The trace-projection approach is REJECTED on evidence; prompts never
  instruct agents to use `todo`, so no data will appear on its own.
- T2 — Delegation records in `sweave/.sweave/delegations.json`: **198
  records (99 done / 26 review / 73 failed; 79 task / 119 chat)**.
  Verdict: a Kanban board over Delegation status has REAL data today
  with zero new backend contracts.
- T3 — Escalations live as per-delegation JSON under
  `~/.sweave/escalations/` (question/escalation/permission kinds,
  answered/skipped/pending). Verdict: the bugs/audit lane has real data
  via existing endpoints (`GET /api/delegations/{id}/escalation`,
  `?parent_task_id=` listing).
- T4 — No scheduler, no plan/ticket store, no `skills/` dir anywhere.
  Anything scheduled or ticket-shaped is new construction, not plumbing.
- T5 — UI shell: routes `/chat /children /delegations/:id /memory
  /agents /settings` (`sweave-web/src/App.tsx:40-57`), Sidebar
  Chat/Children/Memory/Agents/Settings (`Sidebar.tsx:39-46`), R4.1
  `ScaffoldPage` pattern for honest stubs. A `/plan` route + sidebar
  entry follows the established pattern; no shell rework.

## 2. Goal state

- `/plan`: Kanban (columns from Delegation status) + Table (same records,
  sortable) + Bugs lane (failed delegations + open escalations). Read-only
  in Phase A; user-created lightweight tickets in Phase B.
- Tickets: `{title, body, status: open→doing→done, reporter: user|agent,
  links: {delegation_ids, session_ids}}` — no workflow engine, no
  assignments, no sprints (user ruling: no full ticket weight).
- Writes: user via UI; orchestrator files directly (native call on custom
  engine, MCP tool on opencode); specialists/reviewers ESCALATE, never
  file (worktree-jailed by charter; filing is orchestration — ruling to
  lock in Phase B).
- Reads without MCP saturation: `skills/tickets/SKILL.md` +
  `skills/plan/SKILL.md` (R2 convention; first skills besides the
  deferred standalone `customize-sweave`), file-backed records under
  `{project}/.sweave/{tickets,plans}/*.md`, read with existing
  `read/glob`. Zero new always-visible MCP tools.
- Schedule: OPEN (see §5 Q2) — candidate meanings: due dates/reminders
  on tickets vs scheduled/recurring runs. Not designed until the user
  picks one.

## 3. Phases (each shippable, each gated; later phases never reshape earlier ones)

### Phase A — Plan board over Delegation data, read-only (DONE 2026-09-11)
New `/plan` route + Sidebar entry. Three views over EXISTING endpoints
only (`GET /api/delegations`, `GET .../detail`, escalation endpoints):
Kanban columns queued/running/review/done/failed, Table of the same
records, Bugs lane = failed + needs_attention. Pure-function board
builder (`pages/plan/board.ts`, LiveTree pattern) + vitest pinning the
status→column map; WS-pulsed via `delegation.status_changed` (same
subscription pattern as `TurnDelegations`).
Justification: T2+T3 (real data, no new contracts); T1 (why not
trace-todos); T5 (route pattern exists).
Done-gate: board renders the Sweave project's own 198 delegations in a
screenshot gate; empty-project state honest; pytest untouched-green (no
backend change), vitest +, `npm run build` green.

Execution summary (2026-09-11, commit `eeb636d`): shipped as planned, no
deviations. `pages/plan/board.ts` pure builder (columns + bugs lane +
BOARD_CAP 200) + 5 vitest; `pages/Plan.tsx` (Kanban/table toggle, bugs
lane, shared DetailView modal, same `["delegations"]` query key as
Children); route + Sidebar funnel entry + palette item; LiveTree
`KindPill`/`StatusPill` exported (one-word, behavior-neutral) for one
pill convention. Gates: 238/238 vitest (was 233; +5), `npm run build`
green (tsc + vite), 645/645 pytest untouched-green (zero backend files
in the commit). Screenshot gate deferred (no backend running in this
session) — owed before Phase B.

### Phase B — Lightweight tickets store + user creation (next)
Per-project `{project}/.sweave/tickets.json` (atomic write-through,
per-project lock, schema_version=1 — Delegation-schema discipline per
gotcha #12), endpoints
`GET/POST /api/projects/{name}/tickets`,
`POST .../tickets/{id}/close|reopen`, WS `ticket.created/updated`,
UI create/close in the Bugs lane. Orchestrator files via existing
escalation→ticket path; specialists keep escalating (no new tools for
them). Ruling to lock: reviewer finding → escalation → orchestrator
files (no direct specialist write).
Done-gate: ticket round-trip (create→close→reopen) via API + UI,
persisted on disk, WS-pulsed; pytest +N, vitest +M, build green.

### Phase C — Plans spanning sessions + skill reads (after custom engine Step 3)
Per-project plan records linking sessions/delegations
(`{project}/.sweave/plans.json` + per-plan `.md` mirror for skill
reads), `skills/plan/SKILL.md` + `skills/tickets/SKILL.md` pilot
(`customize-sweave` stays standalone/post-MVP per 2026-09-11 ruling),
per-project spec extraction via `build_context()` (AGENTS.md loader —
already in CUSTOM_ENGINE_PLAN Step 3, no separate pipeline).
Done-gate: skill-driven read demonstrated on both harnesses (opencode
via `skill`+`read`, custom via native); plan survives session close.

### Phase D — Schedule: due dates + reminders, visual-only (scoped 2026-09-11)
Tickets/plans carry `due_at`; the schedule view surfaces overdue/upcoming
from the same stores — no background execution, no reminders daemon, no
cron. Scheduled/recurring runs are a deferred future expansion (separate
task page; its scheduled tasks will show in this schedule view). Until
then the schedule, like Phase A, is purely visible.

## 4. Explicit non-goals

- No trace-todo projection UI (rejected: T1).
- No full ticket system (assignments, sprints, workflows, SLAs).
- No specialist direct ticket writes (charter + permission model unchanged).
- No scheduler construction before Q2 is answered.
- No `customize-sweave` skill here (standalone, post-MVP — 2026-09-11).
- No backend change in Phase A (read-only over existing endpoints).

## 5. Validation answers (2026-09-11)

- Q1 — new `/plan` Sidebar tab (own route, Chat/Children weight).
- Q2 — due dates + reminders, visual-only; scheduled runs deferred.
- Q3 — Phase A read-only this session; creation stays in Phase B.

## 6. Risks

- Delegation volume per project is unbounded (198 already in one
  project) — concrete mitigation (why first paint stays fast): single
  project-scoped query (`?project_name=`, archived excluded by default),
  client-side cap (newest 200, "showing N of M" note), client-side
  grouping (no N+1 — detail/escalation endpoints fetch on expand only,
  the same on-demand pattern Children uses), bugs lane from the
  `needs_attention` flag already on the record (no extra fetch per row).
- Bugs lane mixes two sources (failed status vs escalation records) —
  define the merge rule in code (`failed first, then needs_attention`,
  dedup by delegation id) and pin with vitest, or the lane double-counts.
- Phase B store adds a third per-project JSON (delegations, agents,
  tickets) — same locking/atomicity discipline or concurrent
  ticket+delegation writes corrupt on Windows (gotcha: atomic_write).
- Skill reads (Phase C) depend on the skills convention landing; if R2
  reshapes it, the pilot skills reshape with it — file layout is the
  spec, paths are not.
