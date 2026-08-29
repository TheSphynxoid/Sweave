"""Worktree routes (list, clean, remove)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


@router.get("/api/worktrees")
async def list_worktrees(state: AppState = Depends(get_state)):
    worktrees = state.worktree_manager.list_worktrees()
    return {
        "worktrees": [
            {
                "path": str(wt.path),
                "branch": wt.branch,
                "task_id": wt.task_id,
                "agent": wt.agent_name,
                "created_at": wt.created_at.isoformat(),
                "pr_url": wt.pr_url,
            }
            for wt in worktrees
        ]
    }


@router.post("/api/worktrees/clean")
async def clean_worktrees(state: AppState = Depends(get_state)):
    worktrees = state.worktree_manager.list_worktrees()
    for wt in worktrees:
        state.worktree_manager.remove_worktree(wt.task_id, wt.agent_name, force=True)
    if state.event_bus is not None:
        await state.event_bus.publish("worktrees_cleaned", {"count": len(worktrees)})
    else:
        from sweave.web.server import _broadcast

        await _broadcast(state, "worktrees_cleaned", {"count": len(worktrees)})
    return {"success": True, "cleaned": len(worktrees)}


@router.delete("/api/worktrees/{task_id}/{agent_name}")
async def remove_worktree_endpoint(
    task_id: str, agent_name: str, state: AppState = Depends(get_state)
):
    success = state.worktree_manager.remove_worktree(task_id, agent_name, force=True)
    if success:
        if state.event_bus is not None:
            await state.event_bus.publish(
                "worktree_removed", {"task_id": task_id, "agent": agent_name}
            )
        else:
            from sweave.web.server import _broadcast

            await _broadcast(
                state, "worktree_removed", {"task_id": task_id, "agent": agent_name}
            )
    return {"success": success}
