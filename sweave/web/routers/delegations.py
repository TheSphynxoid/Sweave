"""Delegation routes (the v2 async task contract).

M1.prep step 6 wires the :class:`JobRunner` and the v2 async submit. The
synchronous ``POST /api/tasks`` endpoint keeps working for now and is
marked deprecated in OpenAPI; it will be removed once the UI migrates
(M1.4+).

M1.1 step 2: DelegationStores are per-project. The routers below
scan the known stores on read paths (``list``, ``get``) because
``delegation_id`` is unique across all projects and the read API is
global.

M1.1 step 4:
* ``GET /api/delegations`` gains filters: ``?project_name=&status=
  &parent_task_id=`` (M1.1 plan §4.4.1).
* ``POST /api/v2/tasks`` accepts ``parent_task_id`` and ``manifest``
  passthrough (M1.1 plan §4.4.2; generation of the manifest is M1.6).
* ``GET /api/subagent-runs`` + ``POST /api/subagent-runs`` +
  ``POST /api/subagent-runs/{id}/finish`` (M1.1 plan §4.4.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sweave.runtime.delegation_store import Manifest
from sweave.runtime.subagent_store import SubAgentRun, SubAgentStatus
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
    project_name: Optional[str] = None
    # M1.1: deferral chain link (None = orchestrator-initiated).
    parent_task_id: Optional[str] = None
    # M1.1: optional specialist self-report; generation is M1.6 scope.
    manifest: Optional[Manifest] = None


class TaskSubmitV2Response(BaseModel):
    delegation_id: str
    task_id: str
    status: str
    agent: str
    model: str


class SubAgentRunStart(BaseModel):
    agent: str
    purpose: str = "custom"  # explore|investigate|custom
    parent_session_id: Optional[str] = None
    project_name: Optional[str] = None


class SubAgentRunFinish(BaseModel):
    """Terminal-state update for a SubAgentRun. ``status`` is restricted
    to ``done`` / ``failed`` (you cannot "finish" a run by setting it
    back to ``running`` — that would be a no-op transition).
    """
    status: Literal["done", "failed"]
    output_summary: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_stores(state: AppState) -> list:
    """Snapshot of the per-project stores known to the registry."""
    if state.delegation_stores is None:
        return []
    return state.delegation_stores.known_projects_stores()


def _filter_delegations(
    records: list, *,
    project_name: Optional[str],
    status: Optional[str],
    parent_task_id: Optional[str],
) -> list:
    """Apply M1.1 step 4 filters to a list of Delegation records.

    All filters are AND'd; None means "don't filter on this field".
    """
    out = records
    if project_name is not None:
        out = [r for r in out if r.project_name == project_name]
    if status is not None:
        out = [r for r in out if r.status == status]
    if parent_task_id is not None:
        out = [r for r in out if r.parent_task_id == parent_task_id]
    return out


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
    # When the user supplies an explicit ``agent``, capture the router's
    # *fallback* decision so the override log can record the discrepancy
    # (M1.2 step 3; see SpecialistResolver routing later when M1.7 ships).
    routed_agent: str | None = None
    routed_model: str | None = None
    if request.agent:
        agent = request.agent
        model = request.model or state.config_manager.resolve_model(agent)
        # Compute what the router would have picked (for the override log)
        try:
            decision = state.router.route(request.task)
            routed_agent = decision.agent
            routed_model = state.config_manager.resolve_model(decision.agent)
        except Exception:
            pass
    else:
        decision = state.router.route(request.task)
        agent = decision.agent
        model = request.model or decision.model
        routed_agent = agent
        routed_model = model

    # Project pin: explicit request wins, else fall back to the active
    # project so the runner can file the record correctly.
    project_name = request.project_name
    if project_name is None:
        project_name = state.active_project_name()

    # M1.6 step 2: deferral chain validation. When ``parent_task_id`` is
    # set, the new delegation is a child; the DelegationManager enforces
    # depth / loop / budget before we hand off to the JobRunner. The
    # chain rules raise specific ChainError subclasses; we map each to
    # the right HTTP code + a "rejected: <reason>" string so the MCP
    # ``defer`` tool can surface the actionable error verbatim.
    if request.parent_task_id:
        if state.delegation_manager is None:
            raise HTTPException(503, "delegation manager not initialised")
        # Look up the parent in the per-project stores. The parent's
        # agent name is what we check for loops; its chain_root_id
        # establishes which cache the new delegation lives under.
        from sweave.runtime.delegation_manager import (
            BudgetExceededError,
            DepthExceededError,
            LoopDetectedError,
        )

        parent = None
        for store in _all_stores(state):
            parent = store.get(request.parent_task_id)
            if parent is not None:
                break
        if parent is None:
            # Plan ruling: parent is required for defer; a defer with
            # an unknown parent_task_id is a 404 (caller is using a stale
            # id, or the orchestrator's own delegation was deleted).
            raise HTTPException(
                404,
                f"parent delegation '{request.parent_task_id}' not found",
            )
        try:
            new_delegation = state.delegation_manager.validate(
                parent=parent,
                target=agent,
                task=request.task,
                reason=(
                    request.manifest.get("intent", "")
                    if isinstance(request.manifest, dict)
                    else ""
                ),
            )
        except DepthExceededError as e:
            raise HTTPException(409, f"rejected: {e}") from e
        except LoopDetectedError as e:
            raise HTTPException(409, f"rejected: {e}") from e
        except BudgetExceededError as e:
            raise HTTPException(409, f"rejected: {e}") from e
    else:
        new_delegation = None

    delegation = await state.job_runner.submit(
        agent=agent,
        task=request.task,
        model=model,
        parent_session_id=request.parent_session_id,
        project_name=project_name,
        parent_task_id=request.parent_task_id,
        manifest=request.manifest,
        depth=new_delegation.depth if new_delegation else 0,
        chain_root_id=new_delegation.chain_root_id if new_delegation else None,
        coordination_tokens=new_delegation.coordination_tokens if new_delegation else 0,
    )

    # M1.2 step 3: append an override log entry if the user supplied an
    # explicit ``agent`` that differs from the router's decision. The
    # log is observability for R6 dispatch training; never block the
    # submit on it.
    if request.agent and routed_agent and request.agent != routed_agent:
        from pathlib import Path

        from sweave.web.routers.specialists import record_override_if_differing

        proj_dir: Path | None = None
        if project_name is not None:
            from sweave.projects import project_manager

            proj = project_manager.get_project(project_name)
            if proj is not None:
                proj_dir = Path(proj.path)
        await record_override_if_differing(
            state=state,
            project_dir=proj_dir,
            session_id=request.parent_session_id,
            task=request.task,
            routed_agent=routed_agent or "",
            routed_model=routed_model,
            user_agent=agent,
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
async def list_delegations(
    project_name: Optional[str] = None,
    status: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    state: AppState = Depends(get_state),
):
    """List delegations, newest first. Optional filters:
    ``?project_name=`` / ``?status=`` / ``?parent_task_id=``.
    """
    records: list = []
    for store in _all_stores(state):
        records.extend(store.list())
    records = _filter_delegations(
        records,
        project_name=project_name,
        status=status,
        parent_task_id=parent_task_id,
    )
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


# ---------------------------------------------------------------------------
# M1.4+M1.5 step 3: Human promotion (review -> done)
# ---------------------------------------------------------------------------
#
# A delegation that reaches ``review`` stays there until a human
# promotes it. R2's cross-review will call this same endpoint
# programmatically (the API is the automation seam). The plan
# (``docs/M1_4_5_PLAN.md`` step 3) extends the "human merges" rule
# to lifecycle promotion: the only path to ``done`` is this endpoint.
# ---------------------------------------------------------------------------


@router.post("/api/delegations/{delegation_id}/promote")
async def promote_delegation(
    delegation_id: str, state: AppState = Depends(get_state)
):
    """Promote a delegation from ``review`` to ``done`` (M1.4+M1.5 step 3).

    Only valid from ``review``; any other status returns 409. The
    delegation store is updated, the trace records ``status_changed``,
    ``delegation.status_changed`` is published on the WS event bus,
    and the bridged ``ChildSession.status`` (if any) is updated to
    ``done`` so the UI's Children tab reflects the new state.
    """
    # Find the delegation across all known per-project stores.
    for store in _all_stores(state):
        rec = store.get(delegation_id)
        if rec is None:
            continue
        if rec.status != "review":
            raise HTTPException(
                409,
                f"delegation '{delegation_id}' is in status '{rec.status}'; "
                "only 'review' can be promoted to 'done'",
            )
        # Update the store. Use the same field set the runner uses
        # for its own _transition: status, completed_at, updated_at.
        from datetime import datetime as _dt
        await store.update(
            delegation_id,
            status="done",
            completed_at=_dt.now(),
        )
        # Trace + WS: mirror the runner's _transition vocabulary so
        # observers (UI, R6) get the same shape they already consume.
        if state.event_bus is not None:
            await state.event_bus.publish(
                "delegation.status_changed",
                {
                    "delegation_id": delegation_id,
                    "status": "done",
                    "agent": rec.agent,
                    "task_id": rec.task_id,
                },
            )
        # Trace log: same shape as JobRunner._transition.
        from sweave.runtime.trace_log import TraceLog

        trace = TraceLog(delegation_id, base_dir=state.traces_dir)
        trace.append(
            "status_changed",
            {"status": "done", "agent": rec.agent, "source": "human_promote"},
        )
        trace.close()
        # UI v1 compat bridge: update the ChildSession.status in the
        # parent session so the Children tab re-renders. The bridge
        # write-through is best-effort: a missing parent (orphan
        # delegation) leaves the child stale; R4 removes the bridge.
        _sync_bridged_child_status(state, delegation_id, "done")
        return store.get(delegation_id).to_dict()  # type: ignore[union-attr]
    raise HTTPException(404, f"Delegation '{delegation_id}' not found")


def _sync_bridged_child_status(
    state: AppState, delegation_id: str, new_status: str
) -> None:
    """Update the bridged ChildSession.status for a promotion.

    The runner wrote a ``ChildSession`` carrying ``delegation_id`` on
    submit (M1.1 step 4 bridge). The Children tab reads from the
    session, not the delegation directly, so a status change on the
    delegation needs to be mirrored back to the child entry for the
    UI to update.

    Walk every session known to the project manager; for the one whose
    ``children`` includes a child with our ``delegation_id``, set its
    status and persist. The walk is cheap (sessions are in-memory; the
    typical project has one or two active sessions at a time) and
    avoids needing to thread the parent_session_id through the
    delegation record.
    """
    from sweave.projects import project_manager

    if project_manager is None:
        return
    for proj in project_manager.list_projects():
        for session in project_manager.list_sessions(proj.name):
            mutated = False
            for child in session.children:
                if child.delegation_id == delegation_id:
                    child.status = new_status
                    mutated = True
            if mutated:
                project_manager.save_session(session)


# ---------------------------------------------------------------------------
# SubAgentRun (M1.1 step 4): ephemeral, capped. R2's /investigate is the
# primary consumer; M1.1 ships the API surface but no orchestrator-side
# caller yet. ``project_name`` is the same default-as-v2-task contract
# so the run lands in the active project's audit trail (R2 may consume
# the project_name to scope the read).
# ---------------------------------------------------------------------------


@router.post("/api/subagent-runs")
async def start_subagent_run(
    request: SubAgentRunStart, state: AppState = Depends(get_state)
):
    if state.subagent_runs is None:
        raise HTTPException(503, "SubAgentRunStore not initialised")
    run = SubAgentRun(
        agent=request.agent,
        purpose=request.purpose,
        parent_session_id=request.parent_session_id,
        project_name=request.project_name,
        status="running",
    )
    await state.subagent_runs.add(run)
    return run.to_dict()


@router.get("/api/subagent-runs")
async def list_subagent_runs(
    agent: Optional[str] = None,
    purpose: Optional[str] = None,
    status: Optional[str] = None,
    state: AppState = Depends(get_state),
):
    if state.subagent_runs is None:
        return {"runs": []}
    runs = state.subagent_runs.list()
    if agent is not None:
        runs = [r for r in runs if r.agent == agent]
    if purpose is not None:
        runs = [r for r in runs if r.purpose == purpose]
    if status is not None:
        runs = [r for r in runs if r.status == status]
    return {"runs": [r.to_dict() for r in runs]}


@router.get("/api/subagent-runs/{run_id}")
async def get_subagent_run(run_id: str, state: AppState = Depends(get_state)):
    if state.subagent_runs is None:
        raise HTTPException(503, "SubAgentRunStore not initialised")
    run = state.subagent_runs.get(run_id)
    if run is None:
        raise HTTPException(404, f"SubAgentRun '{run_id}' not found")
    return run.to_dict()


@router.post("/api/subagent-runs/{run_id}/finish")
async def finish_subagent_run(
    run_id: str, request: SubAgentRunFinish, state: AppState = Depends(get_state)
):
    if state.subagent_runs is None:
        raise HTTPException(503, "SubAgentRunStore not initialised")
    run = await state.subagent_runs.update(
        run_id,
        status=request.status,
        output_summary=request.output_summary,
        finished_at=datetime.now(),
    )
    if run is None:
        raise HTTPException(404, f"SubAgentRun '{run_id}' not found")
    return run.to_dict()
