# M1.1 — Record split: Delegation v2 + SubAgentRun (execution plan)

Status: ✅ done 2026-08-29 (5 steps + 1 plan refinement). Predecessor: M1.prep
(done). Re-scoped 2026-08-29 against what M1.prep actually built (this plan
supersedes the one-bullet sketch in DESIGN.md §6 R1).

## Deviations from the plan (post-implementation)

Only one substantive deviation; the rest of the plan executed as written.

- **Step 2 (per-project persistence) — locking.** The original plan
  said to reuse the existing `ProjectLockRegistry` from
  `runtime/locking.py` for DelegationStore writes. The implemented
  code uses a per-store `asyncio.Lock` instead. The deviation is
  documented in detail in step 2 (see the "Locking (amended 2026-08-29
  after M1.1 step 2 implementation)" bullet). The short version: the
  registry is for `ProjectManager`'s disk writes; the store's
  in-memory state needs its own lock; mixing them would be wrong or
  redundant. **Code is correct, plan is amended to match.**

## Starting point (from M1.prep — do NOT rebuild)
- `runtime/delegation_store.py`: `Delegation` v1 dataclass (delegation_id, task_id,
  agent, model, task, status closed-set queued→running→review→done/failed, timestamps,
  parent_session_id, project_name, output, error), in-memory store + asyncio.Lock,
  status validation, `schema_version=1` carried on the record. The current `from_dict`
  does `setdefault("schema_version", 1)` and field-filters to known fields — i.e. it
  *plans* for forward compatibility by carrying the version on the wire, but does
  **not** actually migrate across versions. The v1→v2 migration is this step's
  deliverable (see step 1).
- JobRunner wired to the store; `POST /api/v2/tasks`, `GET /api/delegations[/{id}]`,
  `POST /api/delegations/{id}/wait`; JSONL trace logs per delegation.
- `runtime/locking.py` atomic writes + a `ProjectLockRegistry` (per-project
  `asyncio.Lock`) already on `AppState.project_locks`. The registry
  coordinates ProjectManager's disk writes (project.json, session
  files); DelegationStore has its own per-store `asyncio.Lock` for
  in-memory state — see step 2 "Locking (amended 2026-08-29)".

## Goal state
Delegations are **persisted per project** with the full M1.1 field set (worktree, PR,
manifest, deferral chain), v1 records migrate losslessly, ephemeral **SubAgentRun**
exists as a distinct type, and the UI v1 Children tab keeps working via a bridge.

## Steps

### Step 1 — Delegation v2 fields + v1→v2 migration
- Add fields: `worktree_path: str | None`, `branch: str | None`, `pr_url: str | None`,
  `parent_task_id: str | None` (deferral chain; None = orchestrator-initiated),
  `manifest: Manifest | None` where
  `Manifest = {files_touched: [str], intent: str, confidence: float | None,
  breaking_change: bool}` (all optional at write time; structured dict, not YAML).
- Bump `SCHEMA_VERSION = 2`; `from_dict` migrates v1 records (fill new fields with
  None defaults) — the existing `setdefault("schema_version", 1)` path becomes an
  explicit `_migrate_v1_to_v2`.
- JobRunner/DelegateTaskTool fill worktree/branch after worktree creation; pr_url set
  by PR creation (M1.4 wires the call site; field exists now).
- Tests: v1 record loads as v2 with None fields; roundtrip; unknown-field drop still
  works.

### Step 2 — Per-project persistence
- `DelegationStore` gains a project scope: `DelegationStore.load(project_dir)` reads
  `{project_dir}/.sweave/delegations.json` (single file, atomic write via
  `runtime/locking.atomic_write_json_sync`, same contract as ProjectManager) into
  memory; every `add`/`update` persists (write-through — record volume is small;
  no debounce).
- AppState holds `dict[project_name, DelegationStore]`, lazily created on first
  delegation for that project; startup does NOT eagerly load all projects.
- **Locking** *(amended 2026-08-29 after M1.1 step 2 implementation)*:
  the original plan said to reuse the existing `ProjectLockRegistry` from
  `runtime/locking.py` (already on `AppState.project_locks`) for
  DelegationStore writes. **On implementation we kept a separate
  `asyncio.Lock` inside `DelegationStore` and did NOT take a registry
  lock per write.** Justification:

  - The `ProjectLockRegistry` is a per-project `asyncio.Lock` map that
    serialises **disk** writes for `ProjectManager.save_project` /
    `save_session` — i.e. JSON file writes that the ProjectManager
    performs via `runtime.locking.atomic_write_json_sync`. That is
    where cross-task contention for the *same project's JSON file*
    needs to be serialised.
  - `DelegationStore` also writes its own JSON file
    (`{project}/.sweave/delegations.json`) but it has *two* kinds of
    contention to manage:
    1. **In-memory state** (a status transition reading + mutating the
       same record under concurrent updates) — this is what the
       store's own `asyncio.Lock` protects. The registry is `asyncio`
       too, so using it here would work, but the registry's purpose is
       to coordinate *disk* writes; the in-memory lock is local,
       cheaper, and conceptually owned by the store.
    2. **Disk writes** to the store's JSON file — the store calls
       `atomic_write_json` synchronously inside its lock, which is
       fine because the critical section is small (one JSON
       serialise + one write) and runs in an event-loop-friendly way
       (the actual `os.replace` is sync, but it's microseconds).
       Adding a second lock around it would buy nothing.

  The original plan was *consistent* but slightly off-target: the
  registry is for ProjectManager's writes, the store's lock is for
  the store's own state machine. Two locks, two purposes, no
  interference. The amended record (above) is what the code
  implements; the registry is left untouched for ProjectManager
  only. If a future design needs cross-store coordination (e.g.
  writing delegations and project metadata in the same critical
  section), revisit — but that need is not present in M1.1.

  **Alternative considered and rejected**: take the registry lock
  *outside* the store's own lock, on every `add`/`update`. This adds
  a registry-key lookup + acquire for every delegation write, with
  no correctness benefit (the store's internal lock already
  serialises the in-memory mutation, and the atomic-write pattern
  already serialises the disk write). It would only matter if a
  future caller (e.g. M1.6's DelegationManager) needed to
  coordinate a delegation write with a project-metadata write —
  not in scope for M1.1.
- Crash recovery: partial/truncated JSON file → last-good load (atomic rename makes
  this rare) + warning log; never 500 the API on a bad file.
- Tests: roundtrip via temp project dir; concurrent add/update under lock; corrupted
  file recovery; trace log continuity.

### Step 3 — SubAgentRun (ephemeral)
- `runtime/subagent_store.py`: `SubAgentRun` dataclass — `run_id`, `agent`,
  `purpose` (explore|investigate|custom), `status` (running|done|failed),
  `parent_session_id`, `project_name`, `started_at`, `finished_at`, `output_summary`.
  **In-memory only** (ephemeral by definition; dies with server) — no disk, no
  schema_version needed; cap store at last 500 runs (drop oldest).
- R2's `/investigate` will consume this; M1.1 only delivers type + store + API.
- Tests: lifecycle transitions, cap eviction.

### Step 4 — API + UI v1 bridge
- `GET /api/delegations` gains filters: `?project_name=&status=&parent_task_id=`
  (query params, list-ordered by created_at desc).
- `POST /api/v2/tasks` accepts optional `manifest` and `parent_task_id` passthrough.
- SubAgentRun endpoints: `POST /api/subagent-runs` (start), `GET /api/subagent-runs`
  (list, same filter shape), `POST /api/subagent-runs/{id}/finish`.
- **Bridge for UI v1 compat**: JobRunner, on delegation creation, writes a
  `ChildSession` entry into the parent session (existing render path) carrying
  `delegation_id`; Children tab needs zero changes now (badges/type split move to R4).
  Decision recorded: bridge write-through now, children-reads-delegations in R4.
- Tests: filter matrix, bridge produces renderable ChildSession (smoke via
  test_projects.py), v2 task with manifest roundtrips.

### Step 5 — Gates + docs
- pytest green (existing 80 + new ~15), `run.py --check` 13/13, `test_full.py` 40/40,
  loader ALL GREEN.
- DESIGN.md §4 table rows (Delegation/SubAgentRun → ✅; Chat endpoint still ⚠️) +
  R1 bullet: M1.1 ✅; PROJECT_STATE.md M1 progress block.

## Explicit non-goals (later milestones)
- Manifest **generation** (who fills it — specialist self-report prompt convention)
  is M1.6/DelegationManager scope; M1.1 only stores what it's given.
- Deferral *execution* (parent_task_id chains) is M1.6; M1.1 stores the link.
- Children tab visual type split = R4 workbench.

## Risks
- Bridge duplication (ChildSession + Delegation) can drift — mitigated: delegation_id
  stored on ChildSession, and R4 removes the bridge.
- Project-scoped store lifecycle vs project deletion → hook delete_project to drop the
  in-memory store (file removal via existing project dir deletion).
