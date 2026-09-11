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


@router.get("/api/config")
async def get_config(state: AppState = Depends(get_state)):
    return state.config_manager.get().model_dump(exclude_none=True)


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
    }


@router.post("/api/rules")
async def add_rule(request: RuleAddRequest, state: AppState = Depends(get_state)):
    state.config_manager.add_routing_rule(request.pattern, request.agent, request.model)
    payload = {"pattern": request.pattern, "agent": request.agent, "model": request.model}
    await state.publish("rule_added", payload)
    return {"success": True}


@router.get("/api/harnesses")
async def get_harnesses():
    from sweave.harness import detect_all_harnesses, get_opencode_models

    harnesses = await detect_all_harnesses()
    models = await get_opencode_models()
    return {
        "harnesses": [
            {
                "name": h.name,
                "display_name": h.display_name,
                "command": h.command,
                "version": h.version,
                "providers": h.providers,
                "models": h.models,
            }
            for h in harnesses
        ],
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
