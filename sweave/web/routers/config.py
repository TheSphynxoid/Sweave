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
    return {
        "providers": models.providers,
        "all_models": state.config_manager.get_all_models(),
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
async def api_regenerate_models():
    from sweave.platform import run_no_window

    try:
        result = run_no_window(
            ["python", "scripts/generate_models.py"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to regenerate models: {e}")


@router.post("/api/memory/init")
async def api_init_memory():
    return {
        "success": True,
        "message": "Memory backend configuration updated. Run 'python scripts/setup_hindsight.py init' for full setup.",
    }
