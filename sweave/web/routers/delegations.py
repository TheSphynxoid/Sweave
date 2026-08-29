"""Delegation routes (the v2 async task contract).

M1.prep step 6 wires the :class:`JobRunner` and the v2 async submit. The
synchronous ``POST /api/tasks`` endpoint keeps working for now and is
marked deprecated in OpenAPI; it will be removed once the UI migrates
(M1.4+).

M1.1 step 2: DelegationStores are per-project. The routers below
scan the known stores on read paths (``list``, ``get``) because
``delegation_id`` is unique across all projects and the read API is
global. Filtering by ``project_name`` arrives in step 4.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TaskSubmitV2(BaseModel):
    task: str
    agent: Optional[str] = None
    model: Optional[str] = None
    parent_session_id: Optional[str] = None
    # M1.1: optional project pin. When set, the delegation is filed
    # in that project's per-project store; when None, the runner uses
    # the active project (if any) or falls back to the global store at
    # ~/.sweave/. The full ``?project_name=&status=`` filter on the read
    # side arrives in step 4.
    project_name: Optional[str] = None


class TaskSubmitV2Response(BaseModel):
    delegation_id: str
    task_id: str
    status: str
    agent: str
    model: str


# ---------------------------------------------------------------------------
# Submit (async, recommended)
# ---------------------------------------------------------------------------


@router.post("/api/v2/tasks", response_model=TaskSubmitV2Response)
async def submit_task_v2(
    request: TaskSubmitV2,
    state: AppState = Depends(get_state),
):
    """Submit a task asynchronously. Returns immediately with a delegation id.

    Status transitions are published on the WebSocket event bus as
    ``delegation.status_changed`` events. Poll ``GET /api/delegations/{id}``
    for the final state.
    """
    if state.job_runner is None:
        raise HTTPException(503, "JobRunner not initialised")

    # Resolve agent + model via the same path as the legacy /api/tasks.
    if request.agent:
        agent = request.agent
        model = request.model or state.config_manager.resolve_model(agent)
    else:
        decision = state.router.route(request.task)
        agent = decision.agent
        model = request.model or decision.model

    # Project pin: explicit request wins, else fall back to the active
    # project so the runner can file the record correctly.
    project_name = request.project_name
    if project_name is None:
        active = state.active_project_name() if hasattr(state, "active_project_name") else None
        project_name = active

    delegation = await state.job_runner.submit(
        agent=agent,
        task=request.task,
        model=model,
        parent_session_id=request.parent_session_id,
        project_name=project_name,
    )
    return TaskSubmitV2Response(
        delegation_id=delegation.delegation_id,
        task_id=delegation.task_id,
        status=delegation.status,
        agent=delegation.agent,
        model=delegation.model,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _all_stores(state: AppState) -> list:
    """Snapshot of the per-project stores known to the registry.

    Empty list if the registry isn't initialised (e.g. test fixture
    that bypasses lifespan).
    """
    if state.delegation_stores is None:
        return []
    return state.delegation_stores.known_projects_stores()


@router.get("/api/delegations")
async def list_delegations(state: AppState = Depends(get_state)):
    records = []
    for store in _all_stores(state):
        records.extend(store.list())
    # Newest first; sort by created_at desc (ISO string sorts correctly).
    records.sort(key=lambda d: d.created_at, reverse=True)
    return {"delegations": [d.to_dict() for d in records]}


@router.get("/api/delegations/{delegation_id}")
async def get_delegation(
    delegation_id: str, state: AppState = Depends(get_state)
):
    for store in _all_stores(state):
        rec = store.get(delegation_id)
        if rec is not None:
            return rec.to_dict()
    raise HTTPException(404, f"Delegation '{delegation_id}' not found")


# ---------------------------------------------------------------------------
# Optional convenience: block-wait for a delegation to reach a terminal state.
# Subject to the timeout in the query string (default 30s, max 600s).
# ---------------------------------------------------------------------------


@router.post("/api/delegations/{delegation_id}/wait")
async def wait_for_delegation(
    delegation_id: str,
    timeout: float = 30.0,
    state: AppState = Depends(get_state),
):
    if state.job_runner is None:
        raise HTTPException(503, "JobRunner not initialised")
    timeout = max(0.0, min(timeout, 600.0))
    delegation = await state.job_runner.wait(delegation_id, timeout=timeout)
    if delegation is None:
        raise HTTPException(404, f"Delegation '{delegation_id}' not found")
    return delegation.to_dict()
