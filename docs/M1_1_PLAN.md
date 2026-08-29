# M1.1 — Record split: Delegation v2 + SubAgentRun (execution plan)

Status: planned, not started. Est. ~1–1.5 sessions. Predecessor: M1.prep (done).
Re-scoped 2026-08-29 against what M1.prep actually built (this plan supersedes the
one-bullet sketch in DESIGN.md §6 R1).

## Starting point (from M1.prep — do NOT rebuild)
- `runtime/delegation_store.py`: `Delegation` v1 dataclass (delegation_id, task_id,
  agent, model, task, status closed-set queued→running→review→done/failed, timestamps,
  parent_session_id, project_name, output, error), in-memory store + asyncio.Lock,
  status validation, `schema_version=1` + forward-compatible `from_dict`.
- JobRunner wired to the store; `POST /api/v2/tasks`, `GET /api/delegations[/{id}]`,
  `POST /api/delegations/{id}/wait`; JSONL trace logs per delegation.
- `runtime/locking.py` atomic writes; per-project locks in ProjectManager.

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
  `runtime/locking.py`, same contract as ProjectManager) into memory; every `add`/
  `update` persists (write-through — record volume is small; no debounce).
- AppState holds `dict[project_name, DelegationStore]`, lazily created on first
  delegation for that project; startup does NOT eagerly load all projects.
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
