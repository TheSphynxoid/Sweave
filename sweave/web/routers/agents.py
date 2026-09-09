"""Agent CRUD routes (M1.2 step 4: /api/agents bridge + render fix).

The M1.prep-era /api/agents handler returned a shape that did not
match what ``app.js:loadAgents()`` consumed: dicts vs arrays. The
UI silently rendered an empty Agents tab because ``dict.map`` is
undefined and the throw was swallowed by the async wrapper.

This bridge (M1.2 step 4) returns arrays and routes writes through
the new :class:`SpecialistResolver` so the Agents tab renders
correctly and the description-overwrite bug (``routers/agents.py:131-134``
in M1.prep) is gone. The legacy ``agent_created/updated/deleted``
event names are still emitted for the v1 routers; the new
``specialist.created/updated/deleted`` names from step 3 are
published for the new router (M1.prep's WSEventBus accepts any
string).

Bridge shape contract:
* ``builtin`` -- list of dicts, one per models.yaml role excluding
  ``orchestrator`` (the orchestrator is the supervisor, not a
  routing-pool member). Backed by ``ConfigManager.resolve_model``.
* ``global`` -- list of dicts, resolved specialists (project +
  global) excluding the orchestrator and the seed views.
* ``dynamic`` -- legacy ``dynamic_agents`` dict, exposed as a list
  for back-compat with the v1 /api/agents callers. Empty when no
  legacy entries exist (R4 folds this into the new flow).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sweave.runtime.specialist_store import (
    ORCHESTRATOR_NAME,
    Specialist,
)
from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=63)
    role: str = Field(..., description="Role identifier (not a built-in)")
    model: str = Field(..., description="Model to use")
    system_prompt: str = Field("", description="System prompt for the agent")
    description: str = Field("", description="Human-readable description (label)")
    tools: list[str] = Field(default_factory=list, description="Additional tools")
    harness: str = Field("opencode", description="Harness to use")


class AgentUpdate(BaseModel):
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[list[str]] = None
    harness: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_project_dir(state: AppState) -> "Path | None":
    from sweave.projects import project_manager

    active = project_manager.get_active_project()
    return Path(active.path) if active is not None else None


def _specialist_dict(rec: Specialist) -> dict:
    """Map a Specialist to the /api/agents entry shape (app.js compat)."""
    return {
        "name": rec.name,
        "role": rec.role_ref or rec.name,
        "model": rec.current_model or "",
        "system_prompt": rec.system_prompt,
        "description": rec.description,
        "harness": rec.harness,
        "tools": [],  # Specialist doesn't carry tools today; future field
        "builtin": False,
        "dynamic": True,
    }


# ---------------------------------------------------------------------------
# GET /api/agents -- the bridge (the fix for the render bug)
# ---------------------------------------------------------------------------


@router.get("/api/agents")
async def list_agents(state: AppState = Depends(get_state)):
    config = state.config_manager.get()
    proj_dir = _active_project_dir(state)
    resolver = state.ensure_specialist_resolver()

    # Built-in: one entry per known provider excluding orchestrator.
    # The orchestrator is the supervisor; the Agents tab is for the
    # routing pool (specialists the orchestrator delegates to).
    builtin: list[dict] = []
    # Use a fixed list of built-in role names for backward compatibility
    builtin_roles = ["backend", "frontend", "reviewer"]
    for role in builtin_roles:
        builtin.append({
            "name": role,
            "role": role,
            "model": state.config_manager.resolve_model(role),
            "description": f"Built-in {role} specialist",
            "builtin": True,
            "dynamic": False,
        })

    # Global: resolved specialists (project + global) excluding the
    # orchestrator and excluding seed views. The seed views are read-
    # only views over the on-disk config; the user edits the
    # underlying config.yaml.
    all_specialists = resolver.list_resolved(
        project_dir=proj_dir, include_seeds=False
    )
    # Defensive double-check: drop orchestrator + any scope=="seed"
    # records that might have leaked through (e.g. a future scope value).
    global_entries = [
        _specialist_dict(r)
        for r in all_specialists
        if not r.is_orchestrator and r.scope != "seed"
    ]

    # Dynamic: the legacy ``state.dynamic_agents`` dict, exposed as a
    # list for app.js compat. R4 will fold this into the new flow.
    dynamic: list[dict] = []
    for name, spec in state.dynamic_agents.items():
        if name == ORCHESTRATOR_NAME:
            continue
        dynamic.append({
            "name": spec.name,
            "role": spec.role,
            "model": spec.model,
            "system_prompt": spec.system_prompt,
            "description": (
                spec.system_prompt[:100] + "..."
                if len(spec.system_prompt) > 100
                else spec.system_prompt
            ),
            "builtin": False,
            "dynamic": True,
        })

    return {"builtin": builtin, "global": global_entries, "dynamic": dynamic}


# ---------------------------------------------------------------------------
# POST /api/agents -- route through the specialist store
# ---------------------------------------------------------------------------


@router.post("/api/agents")
async def create_agent(agent: AgentCreate, state: AppState = Depends(get_state)):
    if agent.name == ORCHESTRATOR_NAME:
        raise HTTPException(409, f"name '{ORCHESTRATOR_NAME}' is reserved for the orchestrator")
    # Reject built-in role names (the user uses /api/specialists for those)
    builtin_roles = ["backend", "frontend", "reviewer"]
    if agent.role in builtin_roles:
        raise HTTPException(400, f"Role '{agent.role}' is a built-in role")
    resolver = state.ensure_specialist_resolver()
    proj_dir = _active_project_dir(state)
    rec = Specialist(
        name=agent.name,
        scope="project",
        is_orchestrator=False,
        role_ref=None,  # explicit `role` in the legacy model != models.yaml role
        description=agent.description,
        system_prompt=agent.system_prompt,
        harness=agent.harness or "opencode",
        current_model=agent.model or None,
    )
    try:
        resolver.create(rec, project_dir=proj_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Broadcast the new event name (M1.2 step 3) plus the legacy
    # alias (M1.prep vocab) for any v1 client still listening.
    await state.publish("specialist.created", {"name": rec.name, "scope": rec.scope})
    await state.publish("agent_created", {"name": rec.name, "role": agent.role})
    return {"success": True, "agent": agent.name}


# ---------------------------------------------------------------------------
# GET /api/agents/{name} -- reach into both legacy and new
# ---------------------------------------------------------------------------


@router.get("/api/agents/{name}")
async def get_agent(name: str, state: AppState = Depends(get_state)):
    if name == ORCHESTRATOR_NAME:
        # Orchestrator is auto-seeded on first resolve; if it doesn't
        # exist yet, just return its built-in shape.
        config = state.config_manager.get()
        return {
            "name": ORCHESTRATOR_NAME,
            "role": ORCHESTRATOR_NAME,
            "model": state.config_manager.resolve_model(ORCHESTRATOR_NAME),
            "description": "Project supervisor (singleton; auto-seeded on first use)",
            "harness": "opencode",
            "tools": [],
            "builtin": True,
            "dynamic": False,
        }
    # Check the new resolver first
    resolver = state.ensure_specialist_resolver()
    proj_dir = _active_project_dir(state)
    rec = resolver.resolve(name, project_dir=proj_dir)
    if rec is not None and not rec.is_orchestrator and rec.scope != "seed":
        return _specialist_dict(rec)
    # Legacy dynamic_agents (back-compat)
    if name in state.dynamic_agents:
        spec = state.dynamic_agents[name]
        return {
            "name": spec.name,
            "role": spec.role,
            "model": spec.model,
            "system_prompt": spec.system_prompt,
            "tools": spec.tools,
            "harness": spec.harness,
            "dynamic": True,
        }
    # Built-in role (model tier)
    builtin_roles = ["backend", "frontend", "reviewer"]
    if name in builtin_roles:
        return {
            "name": name,
            "role": name,
            "model": state.config_manager.resolve_model(name),
            "description": f"Built-in {name} specialist",
            "harness": "opencode",
            "tools": [],
            "builtin": True,
            "dynamic": False,
        }
    raise HTTPException(404, f"Agent '{name}' not found")


# ---------------------------------------------------------------------------
# PUT /api/agents/{name} -- description-overwrite bug FIXED
# ---------------------------------------------------------------------------


@router.put("/api/agents/{name}")
async def update_agent(
    name: str, update: AgentUpdate, state: AppState = Depends(get_state)
):
    if name == ORCHESTRATOR_NAME:
        raise HTTPException(409, f"name '{ORCHESTRATOR_NAME}' is reserved for the orchestrator")
    builtin_roles = ["backend", "frontend", "reviewer"]
    if name in builtin_roles:
        raise HTTPException(400, "Cannot update built-in agent")
    resolver = state.ensure_specialist_resolver()
    proj_dir = _active_project_dir(state)
    existing = resolver.resolve(name, project_dir=proj_dir)
    if existing is None:
        raise HTTPException(404, f"Dynamic agent '{name}' not found")
    # Each field is patched independently; description NEVER overwrites
    # system_prompt (the M1.prep bug at routers/agents.py:131-134).
    if update.model is not None:
        existing.current_model = update.model
    if update.system_prompt is not None:
        existing.system_prompt = update.system_prompt
    if update.description is not None:
        # description is its own field on the Specialist; it never touches
        # system_prompt. (M1.0-era put-with-description would clobber the
        # prompt; the M1.2 store treats them as distinct.)
        existing.description = update.description
    if update.harness is not None:
        existing.harness = update.harness
    # tools not yet on Specialist; future field
    try:
        resolver.update(existing, project_dir=proj_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await state.publish("specialist.updated", {"name": existing.name, "scope": existing.scope})
    await state.publish("agent_updated", {"name": name})
    return {"success": True, "agent": name}


# ---------------------------------------------------------------------------
# DELETE /api/agents/{name} -- route through the specialist store
# ---------------------------------------------------------------------------


@router.delete("/api/agents/{name}")
async def delete_agent(name: str, state: AppState = Depends(get_state)):
    if name == ORCHESTRATOR_NAME:
        raise HTTPException(409, f"name '{ORCHESTRATOR_NAME}' is reserved for the orchestrator")
    resolver = state.ensure_specialist_resolver()
    proj_dir = _active_project_dir(state)
    try:
        removed = resolver.delete(name, project_dir=proj_dir)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if not removed:
        raise HTTPException(404, f"Dynamic agent '{name}' not found")
    await state.publish("specialist.deleted", {"name": name})
    await state.publish("agent_deleted", {"name": name})
    return {"success": True}
