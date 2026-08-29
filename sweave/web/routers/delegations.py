"""Delegation routes (the v2 async task contract).

In step 2 this module exists only so the URL prefix is documented and the
``/api/delegations`` endpoints return a clear "not yet wired" response.
Step 6 wires the real :class:`sweave.runtime.job_runner.JobRunner` and
:class:`sweave.runtime.delegation_store.DelegationStore`.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/delegations")
async def list_delegations():
    """List delegations. Not yet wired (lands in M1.prep step 6)."""
    return {
        "delegations": [],
        "_status": "JobRunner lands in M1.prep step 6",
    }


@router.get("/api/delegations/{delegation_id}")
async def get_delegation(delegation_id: str):
    """Get a delegation by id. Not yet wired (lands in M1.prep step 6)."""
    return {
        "delegation_id": delegation_id,
        "_status": "JobRunner lands in M1.prep step 6",
    }


@router.post("/api/v2/tasks")
async def submit_task_v2():
    """Submit a task asynchronously. Not yet wired (lands in M1.prep step 6)."""
    return {
        "_status": "JobRunner lands in M1.prep step 6",
        "hint": "Use POST /api/tasks for the legacy synchronous contract",
    }
