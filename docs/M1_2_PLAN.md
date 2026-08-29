# M1.2 — Specialist store + CRUD (execution plan)

Status: planned, not started. Est. ~1–1.5 sessions. Predecessors: M1.prep ✅,
M1.0 ◐ (per-message model already in harness), M1.1 ✅ (this plan re-scopes against
their reality; supersedes the DESIGN.md §6 R1 bullet).

## Tier framing (Orchestrator / Specialist / SubAgent)

The runtime has three tiers, each with a different role and lifecycle. This
section is the conceptual contract that M1.2 implements the first two of;
the Orchestrator's tool wiring lands in M1.7, the encoder-assisted selection
helper lands in R6.

* **Orchestrator** — *singleton per project*; the supervisor. Owns the
  orchestration toolset (currently empty; M1.6's `defer{target, task}` is the
  first tool, M1.7 wires it into the chat loop, R6 layers encoder hints on
  top). Not user-creatable, not user-deletable. Auto-seeded on first
  `resolve("orchestrator", project)`. Storage: per-project (not a Specialist
  record — *flag* on Specialist, see step 1). The plan uses a single
  `Specialist` dataclass with `is_orchestrator: bool` rather than a separate
  `Orchestrator` type: lower churn, same resolve machinery, and the flag
  is enough to gate the create/delete 409s and the model-picker exclusion.
* **Specialist** — *persistent, named, user-creatable*. The work-doer.
  Each specialist has its own session (M1.3) that resumes across delegations.
  `role_ref` is an **optional hint** to the model-resolve chain
  (`task_override > specialist.current_model > config.resolve_model(role_ref)
  > orchestrator.default`); set or unset, known or unknown, the chain falls
  through gracefully. Users name their own specialists — "sql-expert",
  "ui-builder", "code-reviewer" — and the 4 `sweave/agents/*` seeds are just
  starter specialists the user can edit/delete.
* **SubAgent** — *ephemeral, one-shot*. Already implemented in M1.1
  (`runtime/subagent_store.py: SubAgentRun`, purpose ∈
  {explore, investigate, custom}, capped at 500). Not user-creatable in
  the specialist sense; M1.1's API is the entry point; R2's `/investigate`
  is the primary consumer. M1.2 only references this tier to be explicit
  that it exists and is out of scope for this milestone.

**Routing.** The orchestrator decides which specialist receives a task
via its own LLM-driven tool call (`defer`). The rule-router
(`sweave/router/router.py` + `rules.yaml`) is a *temporary hard-edge
fallback*: it guarantees a result when the orchestrator is unavailable
or unsure. The router returns a recommended *primary*, not a hard
decision; the orchestrator's LLM may override based on the task
context. Encoder-assisted selection (R6) makes the LLM's choice
substantially cheaper and more reliable.

## Deviations from the M1.1 plan (carried forward)

None. M1.1's only deviation (the locking scheme) is documented at
`docs/M1_1_PLAN.md` lines 65-94; M1.2 follows the same single-amendment
discipline if a deviation arises.

## Amendments to this plan (post-audit, applied 2026-08-29)

The M1.2 plan was audited before execution. 7 amendments are baked
into this document (this section is the change log so future readers
can see what was revised without diffing git):

* **A. Seed → `role_ref` boundary clarified.** `sweave/agents/loader.py`
  seeds carry a `role` field (not `role_ref`). The new `Specialist`
  dataclass has a `role_ref` field. The resolution boundary maps the
  seed's `role` → `Specialist.role_ref`. Step 1 sentence added.
* **B. `model.changed` shape correction.** The Starting point previously
  said M1.prep supports `{name, model, scope}` for `model.changed`.
  False: M1.prep defined `{role, model, ts}` (`sweave/web/events.py:22`)
  and the legacy alias uses `{role, model}` (`routers/config.py:50`).
  The `{name, model, scope}` shape is **new in M1.2 step 3**. Step 3
  sentence added.
* **C. New event names added to the unified vocabulary.**
  `specialist.created|updated|deleted` are new (the M1.prep vocab
  only had `agent_*` legacy aliases). The bus accepts any string so
  this works in practice; the docstring is updated to list the new
  names alongside the legacy aliases. One-line vocabulary doc
  update in step 3.
* **D. `.sweave` subdir must exist before per-project files are
  written.** `ProjectManager.save_project` does **not** create it
  today (`sweave/projects.py:369-376`). Step 1 now mandates that the
  store constructor creates `(project_dir / ".sweave")` with
  `parents=True, exist_ok=True`. The same convention applies to
  override_log.jsonl in step 3.
* **E. Anchored `dynamic_agents_path`.** Becomes
  `Path.home() / ".sweave" / "agents.yaml"` (kills the CWD-relative
  gotcha). Legacy in-process state is preserved until import at first
  startup. Step 2 sentence added.
* **F. Override log in no-active-project case.** When no project is
  active, the override log falls back to `~/.sweave/override_log.jsonl`
  (already stated in the plan; clarified as "no-active-project
  submits still get logged" to remove ambiguity).
* **G. `role_ref` is an optional hint, not a hard binding** (per
  the design discussion recorded in chat). `PUT`/`POST` do not
  validate role_ref against `models.yaml`; an unknown role_ref
  silently falls through to the orchestrator's default model. This
  matches DESIGN.md §2.2 ("a specialist references a role for its
  default model and may override with any catalog model") and keeps
  the dynamic-specialist claim honest.

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
    is_orchestrator: bool = False  # True only for the per-project orchestrator
                                   # singleton; gates create/delete (409) +
                                   # the model-picker exclusion in PUT/POST
    role_ref: str | None     # OPTIONAL hint to model resolve; not validated
                             # (see amendment G). Used only when current_model
                             # is unset. None / unknown -> orchestrator default.
    description: str = ""    # human-readable label, distinct from system_prompt
                             # (the M1.0-era PUT bug stuffed description into
                             # system_prompt; this fix lives in step 4)
    system_prompt: str = ""  # empty -> seed prompt / fallback
    harness: str = "opencode"
    current_model: str | None = None   # None -> role_ref hint (if any), else
                                      # orchestrator default
    session_id: str | None = None      # durable opencode session (M1.3 fills)
    created_at / updated_at
    # NOT persisted: status (see below)
```

- **status is derived, never stored**: `idle/running` = does an open Delegation exist
  for this specialist (query DelegationStore)? Derived state cannot drift and resets
  for free on restart. The orchestrator's status is *not* exposed through the
  specialist status surface; the orchestrator is a singleton and its "busy" state is
  the active session, derivable from the chat-loop state in M1.7.
- Storage: global `~/.sweave/agents.yaml` (new schema; legacy import, step 2);
  per-project `{project}/.sweave/agents.json` (same atomic write-through contract as
  delegations.json). **The store constructor creates `(project_dir / ".sweave")` with
  `parents=True, exist_ok=True` (amendment D) — `ProjectManager.save_project` does not
  do this today.** Seeds are never persisted.
- Resolution: `resolve(name, project_name)` → project hit → global hit → seed view
  (scope="seed", read-only) → None. Duplicate names across scopes: nearest scope wins.

## Steps

### Step 1 — Specialist + stores + resolution
- `runtime/specialist_store.py`: `Specialist`, `GlobalSpecialistStore`
  (home-anchored path `Path.home() / ".sweave" / "agents.yaml"`),
  `ProjectSpecialistStore` (per-project, lazy like delegation stores;
  constructor creates `(project_dir / ".sweave")` with `parents=True,
  exist_ok=True` per amendment D), `resolve()` + `list_resolved(project_name)`
  (seeds included, flagged).
- Orchestrator singleton: first `resolve("orchestrator", project)` auto-creates
  the per-project record with `is_orchestrator=True` and `role_ref="orchestrator"`.
  Subsequent `create_specialist(name="orchestrator", ...)` → 409;
  `delete_specialist(name="orchestrator", ...)` → 409. The orchestrator's
  `system_prompt` is seeded from `sweave/agents/orchestrator/config.yaml` (the
  existing seed; **the seed's `role` field becomes the new `Specialist.role_ref`
  at the resolution boundary** — amendment A).
- Seeds: `sweave/agents/{backend,frontend,reviewer}/` resolve as scope="seed"
  with the corresponding `role` mapped to `role_ref`; the orchestrator seed
  is handled separately as above. Seeds are read-only views (no PUT/DELETE
  through the public API; the user edits the underlying config.yaml).
- Tests: resolution order incl. shadowing; singleton auto-seed + 409s on
  create/delete; seed read-only view; roundtrip + migration hooks;
  `is_orchestrator=True` gates the model picker.

### Step 2 — Legacy import + AppState integration + model precedence
- Startup migration: if legacy repo-root `agents.yaml` (or `AppState.dynamic_agents`)
  has entries and the global store file is absent → import each dynamic agent as a
  global Specialist (name, role_ref=None, system_prompt, harness, model→current_model).
  `AppState.dynamic_agents` becomes a **derived view** (kept for the legacy router
  until R4); `dynamic_agents_path` is now anchored to
  `Path.home() / ".sweave" / "agents.yaml"` (amendment E) — the CWD-relative
  `Path("agents.yaml")` is gone. AGENTS.md gotcha #2 is replaced.
- Model precedence in `DelegateTaskTool.execute`:
  `task_override > specialist.current_model > config.resolve_model(role_ref)
  > orchestrator.default` (amendment G — graceful fallback to the legacy
  `config.resolve_model(agent)` path when no specialist record exists; the
  role_ref lookup is a *hint*, never a constraint).
- Fix the legacy `PUT /api/agents` description-overwrites-prompt bug **in the bridge**
  (step 4) — note it here because step 2 owns the spec object lifecycle.
- Tests: migration import; precedence chain (4 levels + the unknown-role_ref
  fallback); path anchoring (run from a foreign CWD; legacy file in CWD does
  not affect the new anchored path).

### Step 3 — /api/specialists CRUD + override logging
- Endpoints: `GET /api/specialists?project=&include_seeds=`, `POST` (scope +
  fields; name collision in scope → 409; orchestrator name → 409; `is_orchestrator`
  is not settable from the API — always False on POST), `GET /{name}`, `PUT /{name}`
  (project-scope edits only unless `?scope=global`; orchestrator is editable
  through PUT just enough to change `current_model` — the model picker is the
  primary UX for the orchestrator's model), `DELETE /{name}` (orchestrator → 409),
  `PUT /{name}/model` (sets current_model; publishes `model.changed` with
  `{name, model, scope}` — **amendment B**, the `{name, model, scope}` shape
  is new in M1.2; M1.prep's `model.changed` carries `{role, model, ts}`).
- **WS events added to the unified vocabulary** (amendment C):
  `specialist.created`, `specialist.updated`, `specialist.deleted` (new names;
  the M1.prep vocabulary only had `agent_*` legacy aliases). The bus accepts
  any string so the new names work today; `sweave/web/events.py:18-22` docstring
  is updated to list the new names alongside the legacy aliases. `model.changed`
  with `{name, model, scope}` joins the vocabulary.
- **Override logging** (active-learning gold labels, R6 training data):
  `{project}/.sweave/override_log.jsonl` (global fallback `~/.sweave/override_log.jsonl`
  when no active project — **amendment F**: no-active-project submits still get
  logged; the fallback path is the common case for fresh installs). Appended
  when `POST /api/v2/tasks` carries an explicit `agent` that differs from the
  router decision:
  `{ts, project, session_id, task, routed_agent, routed_model, user_agent, source}`.
  Read API: `GET /api/overrides?project=` (R6 consumes; trivial now).
- Tests: CRUD matrix + collisions + orchestrator 409s; override log append on
  differing-agent submit; events published.

### Step 4 — /api/agents bridge (UI v1 compat) + the render bug
- **Test first**: headless smoke that the Agents tab actually renders cards
  (playwright pattern from test_sidebar_nav.js; if playwright is not installed
  in this env, a focused pytest using the `client` fixture + DOM inspection via
  the static HTML is fine — the goal is to *watch it fail before fixing*).
  Currently `loadAgents()` reads `data.builtin.map()` / `data.global.map()`
  (`sweave/web/static/js/app.js:366-367`) but the router returns
  `{builtin: dict, dynamic: dict}` (`routers/agents.py:65`) → the tab renders
  empty (TypeError on `dict.map`, error swallowed by the async wrapper).
  Watch it fail, then fix.
- Bridge: `/api/agents` returns `{builtin: [...], global: [...], dynamic: [...]}` —
  arrays now (app.js compat), `global` = resolved specialists excluding the
  orchestrator + the seed views (the user already has the orchestrator via
  a different UI surface; seeds are visible in a future R4 workbench view),
  `dynamic` kept as alias. Create/update/delete map onto the specialist store
  (global scope by default, `?project=` option). The description-overflow bug
  (`routers/agents.py:131-134` overwrites `system_prompt` with `update.description`)
  fixed here with a regression test.
- Gate: smoke test green + `test_full.py` 40/40.

### Step 5 — Gates + docs
- pytest (122 + ~20 new), `run.py --check` 13/13, `test_full.py` 40/40, loader green,
  agents-tab smoke green, M1.0/M1.1 endpoints regression-green.
- DESIGN.md §4 rows (specialist store ✅; dynamic agents folded ⚠️→✅), R1 M1.2 ✅
  with the Orchestrator / Specialist / SubAgent tier framing now in the bullet;
  PROJECT_STATE.md progress block; AGENTS.md: `agents.yaml`-at-root gotcha replaced
  by the anchored-path note (a sentence pointing at `Path.home() / ".sweave" /
  "agents.yaml"`).

## Explicit non-goals
- Durable `session_id` usage (M1.3 shared serve); idle/running enforcement of model
  switches (M1.5 semantics; M1.2 only stores + broadcasts).
- UI workbench (R4 phase 3); manifest/deferral execution (M1.6).
- **Orchestrator tool wiring** — the orchestrator currently has no tools. M1.6 adds
  `defer{target, task}` as the first orchestration tool. M1.7 wires it into the
  chat loop. R6 layers encoder-assisted selection on top. **M1.2's
  `is_orchestrator=True` flag is a *marker*, not a toolset.** The orchestrator's
  prompt (seeded from `sweave/agents/orchestrator/config.yaml`) may already mention
  the defer protocol; M1.2 doesn't add anything to the prompt.
- **LLM-driven routing via the orchestrator's tool call** — that story lands in
  M1.7. Until then, the rule-router remains the primary routing surface
  (M1.7's wire-up will make the rule-router a *fallback hint* that the
  orchestrator's LLM may override; the existing rules.yaml + RuleRouter
  code is unchanged in M1.2).
- Fine-grained RBAC/auth (explicit non-goal until R5+).

## Risks
- Legacy import is one-way (global store becomes source of truth) — mitigation: import
  only when target file absent; legacy file left in place untouched.
- Bridge shape drift vs app.js — mitigated by the step-4 render test running in the
  standard gate suite.
- Derived status queries hit DelegationStore per request — volume trivial; add a
  cached view only if measured.
- **Orchestrator-as-flag vs Orchestrator-as-type.** We chose a flag on
  `Specialist` to keep the resolve machinery uniform and avoid a second
  per-project record type. If M1.7 finds the orchestrator's tool wiring
  needs a substantially different schema (separate prompt storage, tool
  allow-list, etc.) we'll split the type. The flag is forward-compatible
  with that split: every check against `is_orchestrator` survives
  unchanged, and the orchestrator's record moves to its own dataclass
  with the same persisted fields.
- **Rule-router drift between M1.2 and M1.7.** M1.2 leaves the router
  unchanged (still the primary routing surface). M1.7 will demote it
  to a hint that the orchestrator's LLM may override. The router code
  doesn't change in M1.2, so any drift lives in M1.7's plan — not
  here.
