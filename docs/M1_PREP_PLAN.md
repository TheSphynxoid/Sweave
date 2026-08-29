# M1.prep — Backend Foundations (Execution Plan)

> Source of truth: `DESIGN.md` §6 R1 / M1.prep. Date: 2026-08-29.
> This file is the **execution contract** for the M1.prep step. Code wins on conflict.

## Scope (in)

1. Server split into router modules + FastAPI `lifespan` app state — no import-time singletons
2. In-process asyncio **JobRunner** for delegations — Delegation records ARE the queue (no broker)
3. Atomic JSON writes + per-project lock in `ProjectManager`
4. Unified `/ws` event vocabulary (`delegation.status_changed`, `specialist.idle/running`, `model.changed`)
5. Structured per-delegation trace logs (one JSONL file per delegation: prompts, outputs, status transitions)
6. `tests/` pytest skeleton with ports of the logic-test scripts

## Scope (out — later milestones)

- M1.0 live serve probe
- M1.1 `Delegation` / `SubAgentRun` record split (we define a *minimal* `Delegation` record in prep so the runner has somewhere to write; full schema arrives in M1.1)
- M1.2 specialist store
- M1.3 shared serve + durable context
- M1.4 lifecycle completion
- M1.5 model at request time
- M1.6 deferral protocol
- M1.7 orchestrator chat loop
- M1.8 streaming

## User-locked decisions (2026-08-29)

| Decision | Choice | Rationale |
|---|---|---|
| `POST /api/tasks` | **Keep sync** with old `TaskResponse` shape. **Add** `POST /api/v2/tasks` returning `{delegation_id, status}`. Deprecate sync in OpenAPI; remove in M1.4+ once UI migrates. | Backward compat; new path is the recommended one. |
| `sweave/web/api.py` | **Delete**. | Stale, never mounted, references undefined names. |
| Static assets | **Keep `app.mount("/static", ...)`** as-is. | No risk. |
| Server status | **Check & clean stale PID**. Currently stale (PID 28208 dead, port 8100 not listening, `web.pid` leftover). | Don't pretend a server is running. |

## File layout (new only — unlisted = unchanged)

```
sweave/web/
  server.py                    # shrinks: build_app() only, lifespan, /ws, SPA routes
  state.py                     # NEW: AppState dataclass (lifespan-owned)
  deps.py                      # NEW: FastAPI Depends() helpers pulling from request.app.state
  events.py                    # NEW: WSEventBus (vocabulary + broadcast)
  routers/                     # NEW: split
    __init__.py
    projects.py                # /api/projects/*, /api/sessions/*, /api/memory/banks
    agents.py                  # /api/agents/* (uses dynamic_agents from state)
    tasks.py                   # /api/tasks (sync, deprecated) + /api/v2/tasks (async, new)
    worktrees.py               # /api/worktrees/*
    memory.py                  # /api/memory/recall|retain|reflect
    config.py                  # /api/config, /api/models, /api/rules, /api/harnesses, /api/memory/init, /api/models/regenerate
    fs.py                      # /api/fs/* (DirectoryBrowser)
    delegations.py             # /api/delegations/{id}, GET /api/delegations
  runtime/
    __init__.py
    job_runner.py              # NEW: in-process asyncio queue; submit() returns id, runs DelegateTaskTool
    delegation_store.py        # NEW: minimal Delegation record (schema_version=1)
    trace_log.py               # NEW: per-delegation JSONL trace (prompts/outputs/transitions)
    locking.py                 # NEW: per-project asyncio.Lock registry
sweave/projects.py             # gain: atomic_json_write, per-project locks (helper from runtime/locking.py)
tests/                         # NEW
  __init__.py
  conftest.py                  # tmp ProjectManager base_path fixture
  test_projects_atomic.py
  test_projects_lock.py
  test_job_runner.py
  test_ws_event_bus.py
  test_trace_log.py
  test_routers_smoke.py
docs/M1_PREP_PLAN.md           # NEW: this file
```

## WS event vocabulary (locked for prep, M1.x may add)

| Event name | Payload | Source |
|---|---|---|
| `delegation.status_changed` | `{delegation_id, status, agent, task_id, ts}` | JobRunner |
| `delegation.output_chunk` | `{delegation_id, chunk, ts}` | M1.8 — prep emits nothing here |
| `specialist.idle` | `{name, model, ts}` | M1.3 — prep emits nothing here |
| `specialist.running` | `{name, model, task_id, ts}` | M1.3 — prep emits nothing here |
| `model.changed` | `{role, model, ts}` | config router (already exists; re-emit on the new bus) |
| **Legacy aliases (preserved)** | | |
| `agent_created` / `agent_updated` / `agent_deleted` | existing shape | agents router |
| `rule_added` | existing shape | config router |
| `worktrees_cleaned` / `worktree_removed` | existing shape | worktrees router |
| `task_completed` | existing shape | tasks router (sync path) |

Legacy names remain on the wire so today's UI keeps working; new code is encouraged to use the unified names. A deprecation banner in OpenAPI is fine.

## Atomic JSON write contract

`runtime/locking.py` provides `atomic_write_json(path, data)`:
1. Serialize to `path.with_suffix(path.suffix + ".tmp")`
2. `fsync` the tmp file
3. `os.replace(tmp, path)` — atomic on Windows + POSIX
4. All under a per-project `asyncio.Lock` (lazily created in `ProjectLockRegistry`)

Read path stays lock-free against the in-memory state; only writes and hydrations acquire.

## Delegation record (minimal, schema_version=1)

```python
@dataclass
class Delegation:
    schema_version: int = 1
    delegation_id: str
    task_id: str
    agent: str
    model: str
    task: str
    status: str  # queued | running | review | done | failed
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    parent_session_id: str | None
    project_name: str | None
    output: str
    error: str | None
```

M1.1 will add: `worktree_path`, `branch`, `pr_url`, `parent_task_id`, `manifest`. M1.prep keeps it minimal so the runner has a record but the record matches the runner's needs.

## Verification per step

Each step is followed by:

- `python test_agents_loader.py` — must stay green (loader untouched in prep but sanity check)
- `pytest` — new tests for the step must be green; existing tests must stay green
- `python test_projects.py`, `python test_browser.py`, `python test_full.py`, `python run.py --check` — only required at the end (need live server)

Step 1 doesn't need a running server (lifespan is exercised on boot). Step 2 needs server boot smoke. Step 6 needs server boot smoke + delegation submission test.

## Execution order

1. `feat(state): introduce AppState + lifespan; remove import-time singletons`
2. `refactor(server): split routes into routers/ modules`
3. `feat(projects): atomic JSON writes + per-project asyncio locks`
4. `feat(events): WSEventBus + unified /ws vocabulary`
5. `feat(runtime): per-delegation trace log (JSONL)`
6. `feat(runtime): in-process JobRunner + /api/v2/tasks + /api/delegations`
7. `chore(tests): pytest skeleton; port logic tests`
8. `chore(cleanup): delete sweave/web/api.py, update DESIGN.md + PROJECT_STATE.md`
9. Final: full test sweep + commit

## Open risks

- uvicorn may import-time evaluate modules; the refactor must not import the router modules at server import (or it must do so cheaply). Mitigation: use `APIRouter` in submodules imported by `server.py` after `lifespan` setup.
- `ProjectManager._sessions` dict is mutated by `add_message`; the lock must protect all writes that touch the same session.
- The trace log adds a small I/O cost per delegation. Acceptable for now; can be made async in M1.8 alongside streaming.
