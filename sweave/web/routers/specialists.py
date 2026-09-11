"""M1.2 step 3: /api/specialists CRUD.

Endpoints:
* GET    /api/specialists?project=&include_seeds=          list
* POST   /api/specialists                                   create
* GET    /api/specialists/{name}                            get
* PUT    /api/specialists/{name}?scope=                     update
* DELETE /api/specialists/{name}                            delete
* PUT    /api/specialists/{name}/model                      set model + emit
* GET    /api/overrides?project=                            read override log

Conventions:
* Orchestrator name (`orchestrator`) is reserved; create/delete/put
  via this API return 409. (The orchestrator is auto-seeded by
  ``SpecialistResolver.resolve_orchestrator``.)
* Orchestrator ``is_orchestrator`` cannot be set from the API -- a
  POST that arrives with ``is_orchestrator=True`` is silently coerced
  to False (defence in depth: a malicious client can't escalate).
* ``model.changed`` event payload is ``{name, model, scope}``
  (amendment B -- the M1.prep ``{role, model, ts}`` shape was for
  ``config_manager`` updates, not specialist model changes).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sweave.runtime.override_log import (
    OverrideLog,
    make_override_entry,
)
from sweave.runtime.specialist_store import (
    ORCHESTRATOR_NAME,
    Specialist,
    parse_model_ref,
)
from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SpecialistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=63)
    role_ref: Optional[str] = None
    description: str = ""
    system_prompt: str = ""
    harness: str = "opencode"
    current_model: Optional[str] = None
    scope: str = "project"  # project | global


class SpecialistUpdate(BaseModel):
    role_ref: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    harness: Optional[str] = None
    current_model: Optional[str] = None


class SetModelRequest(BaseModel):
    model: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_active_project_dir(state: AppState) -> "Path | None":
    """Return the active project dir, or None if no project is active."""
    from pathlib import Path

    from sweave.projects import project_manager

    active = project_manager.get_active_project()
    return Path(active.path) if active is not None else None


def _resolver(state: AppState):
    """Return the (lazily constructed) specialist resolver."""
    return state.ensure_specialist_resolver()


def _orchestrator_409(name: str | None = None) -> HTTPException:
    msg = f"name '{ORCHESTRATOR_NAME}' is reserved for the orchestrator"
    if name is not None:
        msg = f"{msg}; cannot {name} the singleton"
    return HTTPException(409, msg)


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


@router.get("/api/specialists")
async def list_specialists(
    project: Optional[str] = None,
    include_seeds: bool = True,
    state: AppState = Depends(get_state),
):
    """List specialists. ``project`` = project_dir string (or None for
    active project). ``include_seeds`` defaults to True."""
    from pathlib import Path

    if project is not None:
        proj_dir: Path | None = Path(project)
    else:
        proj_dir = _resolve_active_project_dir(state)
    out = _resolver(state).list_resolved(project_dir=proj_dir, include_seeds=include_seeds)
    return {"specialists": [s.public_dict() for s in out]}


@router.get("/api/specialists/{name}")
async def get_specialist(name: str, state: AppState = Depends(get_state)):
    proj_dir = _resolve_active_project_dir(state)
    rec = _resolver(state).resolve(name, project_dir=proj_dir)
    if rec is None:
        raise HTTPException(404, f"specialist '{name}' not found")
    return rec.public_dict()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("/api/specialists", status_code=201)
async def create_specialist(
    body: SpecialistCreate, state: AppState = Depends(get_state)
):
    if body.name == ORCHESTRATOR_NAME:
        raise _orchestrator_409("create")
    if body.scope not in ("project", "global"):
        raise HTTPException(400, f"scope must be 'project' or 'global' (got {body.scope!r})")
    # Defence in depth: API cannot create orchestrators.
    # Name-shape violations are a 400 (bad input), not a 500: the
    # Specialist constructor validates the name and raises ValueError.
    try:
        rec = Specialist(
            name=body.name,
            scope=body.scope,
            is_orchestrator=False,  # always false from the API
            role_ref=body.role_ref,
            description=body.description,
            system_prompt=body.system_prompt,
            harness=body.harness,
            current_model=body.current_model,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.current_model is not None and "/" not in body.current_model:
        raise HTTPException(
            400,
            f"current_model must be qualified as 'provider/model' (got {body.current_model!r})",
        )
    proj_dir = _resolve_active_project_dir(state) if body.scope == "project" else None
    try:
        created = _resolver(state).create(rec, project_dir=proj_dir)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await state.publish(
        "specialist.created", {"name": created.name, "scope": created.scope}
    )
    return created.public_dict()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@router.put("/api/specialists/{name}")
async def update_specialist(
    name: str,
    body: SpecialistUpdate,
    scope: Optional[str] = None,
    state: AppState = Depends(get_state),
):
    """PUT updates a specialist's fields. Default scope is the active
    project; ``?scope=global`` allows editing a global record."""
    if name == ORCHESTRATOR_NAME:
        raise _orchestrator_409("update")
    proj_dir = _resolve_active_project_dir(state) if scope != "global" else None
    target_scope = "global" if scope == "global" else "project"
    # Resolve the existing record to copy + update
    resolver = _resolver(state)
    existing = resolver.resolve(name, project_dir=proj_dir if target_scope == "project" else None)
    if existing is None:
        raise HTTPException(404, f"specialist '{name}' not found")
    # Patch the existing record (preserves created_at, session_id, etc.)
    if body.role_ref is not None:
        existing.role_ref = body.role_ref
    if body.description is not None:
        existing.description = body.description
    if body.system_prompt is not None:
        existing.system_prompt = body.system_prompt
    if body.harness is not None:
        existing.harness = body.harness
    if body.current_model is not None:
        existing.current_model = body.current_model
    try:
        resolver.update(existing, project_dir=proj_dir)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await state.publish(
        "specialist.updated",
        {"name": existing.name, "scope": existing.scope},
    )
    return existing.public_dict()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/api/specialists/{name}")
async def delete_specialist(name: str, state: AppState = Depends(get_state)):
    if name == ORCHESTRATOR_NAME:
        raise _orchestrator_409("delete")
    proj_dir = _resolve_active_project_dir(state)
    try:
        removed = _resolver(state).delete(name, project_dir=proj_dir)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if not removed:
        raise HTTPException(404, f"specialist '{name}' not found")
    await state.publish(
        "specialist.deleted", {"name": name}
    )
    return {"success": True, "name": name}


# ---------------------------------------------------------------------------
# Set current_model (the primary M1.5 readiness endpoint)
# ---------------------------------------------------------------------------


@router.put("/api/specialists/{name}/model")
async def set_specialist_model(
    name: str,
    body: SetModelRequest,
    state: AppState = Depends(get_state),
):
    """Sets ``current_model`` and emits ``model.changed`` with the new
    shape ``{name, model, scope}`` (amendment B)."""
    if name == ORCHESTRATOR_NAME:
        raise _orchestrator_409("update")
    proj_dir = _resolve_active_project_dir(state)
    resolver = _resolver(state)
    existing = resolver.resolve(name, project_dir=proj_dir)
    if existing is None:
        raise HTTPException(404, f"specialist '{name}' not found")
    # Resolve the scope from which the record was found (project vs global)
    # The Specialist.scope field is already set (project/global/seed).
    # PUT here only mutates persistent records; the seed view is read-only.
    if existing.scope == "seed":
        raise HTTPException(400, f"'{name}' is a seed view; edit the config.yaml")
    # Reject bare provider names ("gmi") and other unqualified values:
    # they parse to an incomplete ModelRef, silently drop the model
    # override (wire None), and confuse the picker. The UI only offers
    # qualified provider/model ids.
    if "/" not in body.model:
        raise HTTPException(
            400,
            f"model must be qualified as 'provider/model' (got {body.model!r})",
        )
    # Persist the structured ModelRef (M1.13 step 3, ruling
    # 2026-09-10): set_model_ref stores the JSON-encoded ref so
    # provider/model/variant survive round-trip. A bare-string
    # ``current_model = body.model`` decodes elsewhere but wrote the
    # legacy v1 shape effort variants can't round-trip through.
    existing.set_model_ref(parse_model_ref(body.model))
    try:
        resolver.update(existing, project_dir=proj_dir if existing.scope == "project" else None)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await state.publish(
        "model.changed",
        {"name": existing.name, "model": body.model, "scope": existing.scope},
    )
    return existing.public_dict()


# ---------------------------------------------------------------------------
# Override log
# ---------------------------------------------------------------------------


@router.get("/api/overrides")
async def list_overrides(
    project: Optional[str] = None,
    state: AppState = Depends(get_state),
):
    """Read the override log.

    ``project`` = project_dir string. If omitted, returns the global
    fallback log (no-active-project case, amendment F)."""
    from pathlib import Path

    if project is not None:
        log = OverrideLog.for_project(Path(project))
    else:
        log = OverrideLog.global_fallback()
    return {"overrides": log.read()}


# ---------------------------------------------------------------------------
# Internal helper used by the v2 task router to record overrides.
# Lives here (not in routers/delegations.py) so the override-log
# dependency is on the same module as the schema entry.
# ---------------------------------------------------------------------------


async def record_override_if_differing(
    *,
    state: AppState,
    project_dir: "Path | None",
    session_id: str | None,
    task: str,
    routed_agent: str,
    routed_model: str | None,
    user_agent: str,
) -> None:
    """Append an override log entry if user_agent differs from routed_agent.

    No-op when the user didn't override. No exception on log failure
    (the override log is observability, not a critical path).
    """
    if user_agent == routed_agent:
        return
    if project_dir is None:
        log = OverrideLog.global_fallback()
    else:
        log = OverrideLog.for_project(project_dir)
    entry = make_override_entry(
        project=str(project_dir) if project_dir is not None else None,
        session_id=session_id,
        task=task,
        routed_agent=routed_agent,
        routed_model=routed_model,
        user_agent=user_agent,
    )
    await log.append(entry)
