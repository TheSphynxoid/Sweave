# M1.4+M1.5 — Lifecycle promotion + model-at-request-time (merged execution plan)

Status: planned, not started. Est. ~1 session. Rulings locked 2026-08-30:
- **M1.4 is folded into this plan** (M1.3 pre-delivered most of it: completion
  detection, stuck detection v1, real attach, serve lifecycle). Only the remainder
  lives here.
- **Human promotes**: a delegation reaching `review` stays there until the user
  promotes it (`review → done`). R2's cross-review later automates the verdict.
  This extends the human-merges principle to lifecycle promotion.
- Flaky-test fix lands here as step 0.

## Starting point (already true — do not rebuild)
- Per-message model: OpenCode-side done (M1.0); `ModelRef` + `model_ref_to_wire` exist
  in `runtime/specialist_store.py`; `SpecialistRuntime._model_body` builds the v2
  `body["model"]`; submit-time precedence chain wired in M1.2
  (`task_override > specialist.current_model > config.resolve_model(role_ref)` —
  chain's final `orchestrator.default` level added in M1.2's amendment; verify).
- Lifecycle: turn timeout + review transition (M1.3 step 4); ServeRunner TTL + orphan
  sweep (M1.3 step 1); terminal detection = assistant object with parts (M1.0).
- Known issue: `tests/test_m1_3_step3_job_runner_integration.py::
  test_job_runner_runtime_path_legacy_model_string` fails ~1-in-N full runs (passes
  isolated) — order-dependent, shared-state leakage suspected.

## Goal state
ModelRef is a harness-contract type (R3-ready); switch semantics (idle immediate /
running queued) are enforced and tested; humans promote review→done via API + a
Children-tab button; `_active_agents` dead registry removed; no process leaks; gates
green and stable across repeated runs.

## Steps

### Step 0 — Flake fix + dead-registry audit
- Make `test_job_runner_runtime_path_legacy_model_string` hermetic (no cross-test
  shared state: fixture-scoped stores/event bus, no module-level singletons; assert
  the leak source, don't just reorder). Gate: full suite green 3× consecutively.
- Audit `DelegateTaskTool._active_agents` — M1.3's ServeRunner owns process lifecycle;
  if the dict is now dead code, remove it + `attach_agent` remnants (grep consumers
  first). If still referenced, document why.
- Tests: suite × 3 green.

### Step 1 — ModelRef into the harness contract
- Move/alias `ModelRef` (+ `model_ref_to_wire`) to `harness/base.py` as the contract
  type; `specialist_store` re-exports for compatibility (no API break).
- `Message` gains optional `model: ModelRef | None`; `OpenCodeProcess.send` prefers
  `message.model` over `spec.model` (per-message beats spawn-time). `AgentProcess`
  protocol docstring: model-per-request is contract; R3 adapters implement per-
  invocation flags (claude/codex note in docstring).
- Trace: record `model_used` (provider/model string) on delegation completion.
- Tests: message.model precedence over spec.model; contract type import stability.

### Step 2 — Switch semantics: enforced + proven
- `PUT /api/specialists/{name}/model` while specialist **running** (open delegation):
  accepted, stored, and — because every delegation resolves its model at submit —
  automatically applies to the NEXT delegation, never mid-task. Add the explicit test
  proving queued application (submit delegation → switch model → second delegation
  uses new model via mock ServeRunner capture).
- Verify + test the 4-level chain end incl. `orchestrator.default` final fallback
  (specialist with unknown role_ref and no current_model).
- WS `model.changed` already emitted (M1.2) — assert payload shape in tests.

### Step 3 — Human promotion (review → done)
- API: `POST /api/delegations/{id}/promote` — valid only from `review` (409 from any
  other status; 404 unknown id); sets status=done, `completed_at`, trace
  `status_changed`, WS `delegation.status_changed` (existing vocabulary), and updates
  the bridged ChildSession status so UI v1 reflects it.
- UI v1: button on Children-tab delegation entries ("Mark done") visible only for
  `review` records; offsetParent-verified test (sidebar-regression pattern).
- R2 note recorded in plan + DESIGN: cross-review will call the same endpoint
  programmatically — the API is the automation seam.
- Tests: promotion matrix (review→done ok; queued/running/failed → 409), bridge
  status sync, UI smoke.

### Step 4 — Gates + live spot-check + docs
- pytest (269 + ~12 new), `run.py --check` 13/13, `test_full.py` 40/40, loader green;
  full suite 3× consecutive green (step 0 gate holds).
- **Live spot-check** (gmi, tiny): 3 delegations to one test specialist → all reach
  `review`; promote one via API; process count stable (no leaks); TTL still armed.
- Docs: DESIGN §4 rows (lifecycle ✅; model-at-request-time ✅), R1 M1.4 bullet
  marked **folded into M1.5 (this plan)** + M1.5 ✅, §2.1 note "human promotes
  review→done", PROJECT_STATE progress + rulings, AGENTS gotcha only if a new trap
  surfaced.

## Explicit non-goals
- Cross-review automation (R2 — consumes the promote endpoint).
- Heartbeat/staleness upgrades of stuck detection (R6).
- UI workbench polish (R4).

## Risks
- Promotion UI touches app.js Children rendering (bridge-coupled) — keep minimal;
  R4 replaces the tab anyway.
- Removing `_active_agents` could break an unnoticed consumer — grep-gated removal,
  revert-safe (small diff).
