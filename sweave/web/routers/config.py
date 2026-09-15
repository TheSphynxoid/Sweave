"""Config / models / rules / harnesses / memory-init / models-regenerate routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


class ModelUpdateRequest(BaseModel):
    model: str


class RuleAddRequest(BaseModel):
    pattern: str
    agent: str
    model: Optional[str] = None


class TurnRetriesUpdateRequest(BaseModel):
    turn_retries: int


class ReviewFixUpdateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    review_fix_mode: Optional[str] = None
    review_fix_max_rounds: Optional[int] = None


class HarnessDefaultUpdateRequest(BaseModel):
    harness: str


@router.get("/api/config")
async def get_config(state: AppState = Depends(get_state)):
    return state.config_manager.get().model_dump(exclude_none=True)


@router.get("/api/projects/{name}/config/effective")
async def get_effective_project_config(
    name: str, state: AppState = Depends(get_state)
):
    """Effective config for one project (two-file ruling).

    Global defaults + that project's ``.sweave/config.yaml`` overlay,
    plus which top-level sections the overlay provides (so the UI can
    show global-vs-project provenance instead of implying everything
    is global). 404 for unknown projects; no overlay file simply
    returns the global config with an empty overlay list.
    """
    from pathlib import Path

    from sweave.projects import project_manager

    proj = project_manager.get_project(name)
    if proj is None:
        raise HTTPException(404, f"unknown project {name!r}")
    project_dir = Path(proj.path)
    effective = state.config_manager.get_for_project(project_dir)
    return {
        "project": name,
        "config": effective.model_dump(exclude_none=True),
        "overlay_sections": state.config_manager.overlay_sections_for(
            project_dir
        ),
        "overlay_present": bool(
            state.config_manager.overlay_sections_for(project_dir)
        ),
    }


@router.get("/api/models")
async def get_models(state: AppState = Depends(get_state)):
    models = state.config_manager.get_models()
    # Reasoning-effort variants per qualified model id, for the
    # effort dropdown (models without variants get no dropdown).
    # Sourced from the models.meta.json sidecar; best-effort.
    variants: dict[str, list[str]] = {}
    try:
        for qualified_id, entry in state.config_manager.get_model_meta().items():
            names = entry.get("variants") if isinstance(entry, dict) else None
            if names:
                variants[qualified_id] = list(names)
    except Exception:
        variants = {}
    return {
        "providers": models.providers,
        "all_models": state.config_manager.get_all_models(),
        "variants": variants,
        # The global default: the orchestrator's model and the
        # fallback for specialists without an explicit current_model.
        # Settable via POST /api/models (the Settings Models tab).
        "default": state.config_manager.get_default_model(),
    }


@router.post("/api/models")
async def set_model(request: ModelUpdateRequest, state: AppState = Depends(get_state)):
    # Sets the GLOBAL default model (the orchestrator's model and the
    # fallback for specialists without an explicit current_model).
    # Per-specialist overrides live on /api/specialists/{name}/model.
    try:
        default = state.config_manager.set_default_model(request.model)
    except ValueError as e:
        raise HTTPException(400, str(e))
    payload = {"model": default}
    await state.publish("model_changed", payload)
    return {"success": True, "model": default, "default": default}


@router.get("/api/rules")
async def get_rules(state: AppState = Depends(get_state)):
    routing = state.config_manager.get_routing()
    return {
        "routes": [
            {"pattern": r.pattern, "agent": r.agent, "model": r.model}
            for r in routing.routes
        ],
        "fallback": routing.fallback,
        # Routing scalars (the retry knob lives here — rules.yaml is
        # the writable home; config.yaml's routing block is
        # superseded at load). Surfaced so a "stuck at 0" value is
        # visible without reading files.
        "turn_retries": routing.turn_retries,
        "turn_timeout_s": routing.turn_timeout_s,
        "chain_budget": routing.chain_budget,
        "max_depth": routing.max_depth,
        "review_fix_mode": routing.review_fix_mode,
        "review_fix_max_rounds": routing.review_fix_max_rounds,
    }


@router.put("/api/rules/retries")
async def set_turn_retries(
    request: TurnRetriesUpdateRequest, state: AppState = Depends(get_state)
):
    """Set the global provider-call retry budget (retries AFTER the
    first attempt; 0 disables, max 10).

    Persists to rules.yaml and hot-reloads the running JobRunner +
    ChatLoop singletons. Per-project ``.sweave/config.yaml`` overlays
    still win per turn — when a turn still shows 0 after this call,
    check ``GET /api/projects/{name}/config/effective`` for that
    turn's project overlay.
    """
    try:
        value = state.config_manager.set_turn_retries(request.turn_retries)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"success": True, "turn_retries": value}


@router.put("/api/rules/review-fix")
async def set_review_fix(
    request: ReviewFixUpdateRequest, state: AppState = Depends(get_state)
):
    """Set the review fix-round posture (user toggle, never LLM).

    ``review_fix_mode``: ``direct`` (request_changes spawns the fix
    child immediately — fire-and-forget default) or ``supervised``
    (verdict records only; a human spawns via the fix-round
    endpoint). ``review_fix_max_rounds``: ping-pong bound (0
    disables fix rounds). Either may be omitted. Persists to
    rules.yaml; per-project ``.sweave/config.yaml`` routing overlays
    still win per turn.
    """
    try:
        value = state.config_manager.set_review_fix(
            mode=request.review_fix_mode,
            max_rounds=request.review_fix_max_rounds,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"success": True, **value}


@router.post("/api/rules")
async def add_rule(request: RuleAddRequest, state: AppState = Depends(get_state)):
    state.config_manager.add_routing_rule(request.pattern, request.agent, request.model)
    payload = {"pattern": request.pattern, "agent": request.agent, "model": request.model}
    await state.publish("rule_added", payload)
    return {"success": True}


@router.put("/api/harness/default")
async def set_harness_default(
    request: HarnessDefaultUpdateRequest, state: AppState = Depends(get_state)
):
    """Set the global harness default (``sweave-engine`` | ``opencode``).

    Persists to config.yaml ``harness.default`` and hot-reloads the
    running ``SpecialistRuntime.harness_default``. This is only the
    *config tier*: the specialist record, the project overlay
    (``harness.default``), and the per-task override all still win
    per turn — seed specialists already run on ``sweave-engine``,
    so flipping this to ``opencode`` will not move them. Persisted
    ``"opencode"`` specialist records keep it (change those on the
    Agents tab).
    """
    try:
        value = state.config_manager.set_harness_default(request.harness)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"success": True, "harness": value}


@router.get("/api/harnesses")
async def get_harnesses():
    from sweave.harness import detect_all_harnesses, get_opencode_models

    harnesses = await detect_all_harnesses()
    models = await get_opencode_models()
    entries = [
        {
            "name": h.name,
            "display_name": h.display_name,
            "command": h.command,
            "version": h.version,
            "providers": h.providers,
            "models": h.models,
        }
        for h in harnesses
    ]
    # The native engine is not an external binary, so the detector
    # above never sees it — but it is always available (zero-dep
    # Node sidecar, spawned lazily) and it is the default harness
    # since the step-4 flip. List it first so pickers default sanely
    # even when no external binary is installed.
    if not any(e["name"] == "sweave-engine" for e in entries):
        from sweave.engine.protocol import ENGINE_HARNESS_NAME, PROTOCOL_VERSION

        entries.insert(
            0,
            {
                "name": ENGINE_HARNESS_NAME,
                "display_name": "Sweave Engine",
                "command": "sweave-engine (native sidecar)",
                "version": f"protocol {PROTOCOL_VERSION}",
                # The engine serves the global registry (Models tab),
                # not a per-harness model list — pickers merge the
                # registry independently (Agents page modelOptions).
                "providers": [],
                "models": [],
            },
        )
    return {
        "harnesses": entries,
        "opencode_models": models,
    }


@router.post("/api/models/regenerate")
def api_regenerate_models(state: AppState = Depends(get_state)):
    """Regenerate models.yaml from models.dev + the serve overlay.

    Fast-track 2026-09-11: this calls ``sync_registry`` in-process
    (the same path as ``sweave models sync``) — the old implementation
    shelled out to ``scripts/generate_models.py`` WITHOUT ``--write``,
    so it printed the registry to its own stdout and never touched
    the file. Sync output is providers-only; the user's default in
    config.yaml survives by construction (nothing here reads it).

    Plain ``def`` (not ``async``) so FastAPI runs the blocking fetch
    + scratch-serve boot in the threadpool instead of stalling the
    event loop. Still slow (models.dev fetch + serve boot) — the UI
    should treat it as a long action.
    """
    from pathlib import Path

    from sweave.models_sync import sync_registry

    registry_path = Path(state.config_manager.get().models.registry_path)
    try:
        report = sync_registry(registry_path)
    except RuntimeError as e:
        raise HTTPException(500, f"models regenerate failed: {e}")
    return {
        "success": True,
        "providers": report["providers"],
        "models": report["models"],
        "added": report["added"],
        "removed": report["removed"],
        "source": report["source"],
        "path": report["path"],
    }


@router.post("/api/memory/init")
async def api_init_memory():
    return {
        "success": True,
        "message": "Memory backend configuration updated. Run 'python scripts/setup_hindsight.py init' for full setup.",
    }
