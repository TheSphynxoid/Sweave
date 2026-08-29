"""Delegation routes (the v2 async task contract).

M1.prep step 6 wires the :class:`JobRunner` and the v2 async submit. The
synchronous ``POST /api/tasks`` endpoint keeps working for now and is
marked deprecated in OpenAPI; it will be removed once the UI migrates
(M1.4+).
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

    delegation = await state.job_runner.submit(
        agent=agent,
        task=request.task,
        model=model,
        parent_session_id=request.parent_session_id,
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


@router.get("/api/delegations")
async def list_delegations(state: AppState = Depends(get_state)):
    if state.delegation_store is None:
        return {"delegations": []}
    return {
        "delegations": [d.to_dict() for d in state.delegation_store.list()]
    }


@router.get("/api/delegations/{delegation_id}")
async def get_delegation(
    delegation_id: str, state: AppState = Depends(get_state)
):
    if state.delegation_store is None:
        raise HTTPException(503, "DelegationStore not initialised")
    delegation = state.delegation_store.get(delegation_id)
    if delegation is None:
        raise HTTPException(404, f"Delegation '{delegation_id}' not found")
    return delegation.to_dict()


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
