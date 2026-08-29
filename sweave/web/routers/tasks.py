"""Task execution + routing-decision routes.

Today this module owns the synchronous ``POST /api/tasks`` endpoint (the
old contract: returns the full result in the response). The async
``POST /api/v2/tasks`` endpoint and ``/api/delegations`` routes land in step 6
(JobRunner + DelegationStore).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


class TaskRequest(BaseModel):
    task: str
    agent: Optional[str] = None
    model: Optional[str] = None


class TaskResponse(BaseModel):
    success: bool
    agent: str
    task_id: str
    output: str
    error: Optional[str] = None


class RoutingRequest(BaseModel):
    task: str


class RoutingResponse(BaseModel):
    agent: str
    model: str
    confidence: float
    reasoning: str
    matched_rule: Optional[str] = None


@router.post("/api/tasks", response_model=TaskResponse)
async def run_task(request: TaskRequest, state: AppState = Depends(get_state)):
    """Synchronous task execution. **Deprecated** — use ``POST /api/v2/tasks``."""
    if not state.router or not state.delegate_tool:
        raise HTTPException(503, "Services not initialized")
    if request.agent:
        decision = state.router._llm_fallback(request.task)
        decision.agent = request.agent
        decision.model = request.model or state.config_manager.resolve_model(request.agent)
    else:
        decision = state.router.route(request.task)
        if request.model:
            decision.model = request.model
    result = await state.delegate_tool.execute(
        agent=decision.agent,
        task=request.task,
        model=request.model or decision.model,
    )
    await state.publish(
        "task_completed",
        {
            "task": request.task,
            "agent": result.agent,
            "task_id": result.task_id,
            "success": result.success,
        },
    )
    return TaskResponse(
        success=result.success,
        agent=result.agent,
        task_id=result.task_id,
        output=result.output,
        error=result.error,
    )


@router.post("/api/route", response_model=RoutingResponse)
async def route_task(request: RoutingRequest, state: AppState = Depends(get_state)):
    decision = state.router.route(request.task)
    return RoutingResponse(
        agent=decision.agent,
        model=decision.model or "",
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        matched_rule=decision.matched_rule.pattern if decision.matched_rule else None,
    )
