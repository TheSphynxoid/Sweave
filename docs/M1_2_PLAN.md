# M1.2 — Specialist store + CRUD (execution plan)

Status: planned, not started. Est. ~1–1.5 sessions. Predecessors: M1.prep ✅,
M1.0 ◐ (per-message model already in harness), M1.1 ✅ (this plan re-scopes against
their reality; supersedes the DESIGN.md §6 R1 bullet).

## Starting point (do NOT rebuild)
- `AppState.dynamic_agents: dict[str, AgentSpec]` + `dynamic_agents_path` (CWD-relative
  `Path("agents.yaml")`) + `save_dynamic_agents()` — the legacy dynamic-agent mechanism
  to **fold**, not parallel.
- `routers/agents.py`: `/api/agents` CRUD over dynamic_agents; UI v1 Agents tab is its
  only consumer. **Known bugs fixed in step 4** (below).
- `agents/loader.py` seed definitions (read-only views).
- Per-message model in `opencode.py` (`body["model"] = {providerID, modelID}`) —
  `current_model` feeds it directly.
- WSEventBus (legacy names preserved; new events use the §M1.prep vocabulary).
- M1.1 patterns to copy: per-project store + asyncio.Lock, schema_version +
  from_dict migration, atomic write-through, in-memory overlay for runtime state.

## Goal state
Specialists are first-class: identity + config persisted per scope (project / global),
resolved project → global → seeds, orchestrator a protected per-project singleton,
model precedence chain enforced at submit time, overrides logged as gold labels,
UI v1 Agents tab renders correctly through a bridge.

## Data model

```python
@dataclass
class Specialist:            # persisted identity + config
    schema_version: int = 1  # specialists files carry their own version
    name: str                # unique key within its scope
    scope: str               # "project" | "global" | "seed" (seed = derived view)
    role_ref: str | None     # models.yaml role for default-model resolution
    description: str = ""
    system_prompt: str = ""  # empty -> seed prompt / fallback
    harness: str = "opencode"
    current_model: str | None = None   # None -> role_ref default
    session_id: str | None = None      # durable opencode session (M1.3 fills)
    created_at / updated_at
    # NOT persisted: status (see below)
```

- **status is derived, never stored**: `idle/running` = does an open Delegation exist
  for this specialist (query DelegationStore)? Derived state cannot drift and resets
  for free on restart.
- Storage: global `~/.sweave/agents.yaml` (new schema; legacy import, step 2);
  per-project `{project}/.sweave/agents.json` (same atomic write-through contract as
  delegations.json). Seeds are never persisted.
- Resolution: `resolve(name, project_name)` → project hit → global hit → seed view
  (scope="seed", read-only) → None. Duplicate names across scopes: nearest scope wins.

## Steps

### Step 1 — Specialist + stores + resolution
- `runtime/specialist_store.py`: `Specialist`, `GlobalSpecialistStore`
  (home-anchored path), `ProjectSpecialistStore` (per-project, lazy like delegation
  stores), `resolve()` + `list_resolved(project_name)` (seeds included, flagged).
- Orchestrator singleton: first `resolve("orchestrator", project)` auto-creates the
  project record (role_ref="orchestrator"); create/delete of that name → 409.
- Tests: resolution order incl. shadowing; singleton auto-seed; seed read-only view;
  roundtrip + migration hooks.

### Step 2 — Legacy import + AppState integration + model precedence
- Startup migration: if legacy repo-root `agents.yaml` (or AppState.dynamic_agents)
  has entries and the global store file is absent → import each dynamic agent as a
  global Specialist (name, role_ref=None, system_prompt, harness, model→current_model).
  `AppState.dynamic_agents` becomes a **derived view** (kept for the legacy router
  until R4); `dynamic_agents_path` anchored absolute (kills the CWD-relative gotcha).
- Model precedence in `DelegateTaskTool.execute`:
  `task_override > specialist.current_model > config.resolve_model(role_ref)`
  (graceful fallback to legacy behavior when no specialist record exists).
- Fix the legacy `PUT /api/agents` description-overwrites-prompt bug **in the bridge**
  (step 4) — note it here because step 2 owns the spec object lifecycle.
- Tests: migration import; precedence chain (3 levels); path anchoring (run from
  foreign CWD).

### Step 3 — /api/specialists CRUD + override logging
- Endpoints: `GET /api/specialists?project=&include_seeds=`, `POST` (scope +
  fields; name collision in scope → 409; role_ref must exist in models.yaml),
  `GET /{name}`, `PUT /{name}` (project-scope edits only unless `?scope=global`),
  `DELETE /{name}` (orchestrator → 409), `PUT /{name}/model` (sets current_model;
  publishes `model.changed` {name, model, scope}).
- WS events: `specialist.created|updated|deleted` + `model.changed`.
- **Override logging** (active-learning gold labels, R6 training data):
  `{project}/.sweave/override_log.jsonl` (global fallback `~/.sweave/override_log.jsonl`
  when no active project). Appended when `POST /api/v2/tasks` carries an explicit
  `agent` that differs from the router decision:
  `{ts, project, session_id, task, routed_agent, routed_model, user_agent, source}`.
  Read API: `GET /api/overrides?project=` (R6 consumes; trivial now).
- Tests: CRUD matrix + collisions + orchestrator 409s; override log append on
  differing-agent submit; events published.

### Step 4 — /api/agents bridge (UI v1 compat) + the render bug
- **Test first**: headless smoke that the Agents tab actually renders cards
  (playwright pattern from test_sidebar_nav.js). Currently `loadAgents()` reads
  `data.builtin.map()` / `data.global.map()` but the router returns
  `{builtin: dict, dynamic: dict}` → the tab renders empty (console error swallowed).
  Watch it fail, then fix.
- Bridge: `/api/agents` returns `{builtin: [...], global: [...], dynamic: [...]}` —
  arrays now (app.js compat), `global` = resolved specialists excluding seeds and
  orchestrator, `dynamic` kept as alias. Create/update/delete map onto the specialist
  store (global scope by default, `?project=` option). The description-overflow bug
  fixed here with a regression test.
- Gate: smoke test green + `test_full.py` 40/40.

### Step 5 — Gates + docs
- pytest (122 + ~20 new), `run.py --check` 13/13, `test_full.py` 40/40, loader green,
  agents-tab smoke green.
- DESIGN.md §4 rows (specialist store ✅; dynamic agents folded ⚠️→✅), R1 M1.2 ✅;
  PROJECT_STATE.md progress block; AGENTS.md: `agents.yaml`-at-root gotcha replaced by
  the anchored-path note.

## Explicit non-goals
- Durable `session_id` usage (M1.3 shared serve); idle/running enforcement of model
  switches (M1.5 semantics; M1.2 only stores + broadcasts).
- UI workbench (R4 phase 3); manifest/deferral execution (M1.6).
- Fine-grained RBAC/auth (explicit non-goal until R5+).

## Risks
- Legacy import is one-way (global store becomes source of truth) — mitigation: import
  only when target file absent; legacy file left in place untouched.
- Bridge shape drift vs app.js — mitigated by the step-4 render test running in the
  standard gate suite.
- Derived status queries hit DelegationStore per request — volume trivial; add a
  cached view only if measured.
