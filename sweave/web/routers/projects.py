"""Project + session + memory-banks routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sweave.api.projects import (
    MessageCreate,
    ProjectCreate,
    SessionCreate,
    add_message,
    create_project,
    create_session,
    delete_project,
    delete_session,
    get_active_project,
    get_active_session,
    get_memory_banks,
    get_project,
    get_session,
    list_projects,
    list_sessions,
    set_active_project,
    set_active_session,
)
from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request models (Pydantic validates; services take dataclasses).
# ---------------------------------------------------------------------------


class ProjectCreateRequest(BaseModel):
    name: str
    path: str
    description: str = ""
    # M1.9 step 2: per-project worktree_base override. Default is the
    # global config (set on the project record by the ProjectManager
    # when ``worktree_base`` is omitted; legacy behaviour preserved).
    worktree_base: Optional[str] = None


class SessionCreateRequest(BaseModel):
    name: str
    project_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.post("/api/projects")
async def api_create_project(request: ProjectCreateRequest):
    try:
        project = await create_project(
            ProjectCreate(
                name=request.name,
                path=request.path,
                description=request.description,
                worktree_base=request.worktree_base,
            )
        )
        return {"success": True, "project": project}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/api/projects")
async def api_list_projects():
    return {"projects": await list_projects()}


@router.get("/api/projects/active")
async def api_get_active_project():
    return await get_active_project()


@router.get("/api/projects/{name}")
async def api_get_project(name: str):
    project = await get_project(name)
    if not project:
        raise HTTPException(404, f"Project '{name}' not found")
    return project


@router.post("/api/projects/{name}/active")
async def api_set_active_project(name: str):
    try:
        return await set_active_project(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/api/projects/{name}")
async def api_delete_project(name: str):
    return await delete_project(name)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.post("/api/sessions")
async def api_create_session(request: SessionCreateRequest):
    try:
        session = await create_session(
            SessionCreate(name=request.name, project_name=request.project_name)
        )
        return {"success": True, "session": session}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/api/sessions")
async def api_list_sessions(project_name: Optional[str] = None):
    return {"sessions": await list_sessions(project_name)}


@router.get("/api/sessions/active")
async def api_get_active_session():
    session = await get_active_session()
    return session if session else None


@router.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    session = await get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session '{session_id}' not found")
    return session


@router.post("/api/sessions/{session_id}/active")
async def api_set_active_session(session_id: str):
    try:
        return await set_active_session(session_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    return await delete_session(session_id)


@router.post("/api/sessions/{session_id}/messages")
async def api_add_message(
    session_id: str,
    message: MessageCreate,
    state: AppState = Depends(get_state),
):
    try:
        msg = await add_message(session_id, message)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except TypeError as e:
        raise HTTPException(400, str(e))

    # M1.7 step 2: a user message drives the orchestrator chat loop.
    # The user message is persisted; the loop then runs the
    # orchestrator specialist and persists the assistant reply. Both
    # messages are returned in the response (the user message that
    # was just persisted, plus the assistant message the loop just
    # produced). Non-user roles (system, tool, assistant) skip the
    # loop -- the legacy persist-only contract is preserved.
    if message.role == "user" and state.chat_loop is not None:
        try:
            assistant_msg = await state.chat_loop.run_turn(
                session_id=session_id,
                user_content=message.content,
            )
            return {
                "success": True,
                "message": msg,
                "assistant": assistant_msg,
            }
        except Exception as e:  # noqa: BLE001
            # The loop is best-effort from the router's perspective:
            # if the orchestrator is unreachable, the user message is
            # already persisted. Surface the error so the client can
            # render it. The loop itself persists an explicit
            # assistant error message in this case; the catch here is
            # only for catastrophic loop failures (e.g. session not
            # found -- which the persist call would already have
            # rejected).
            raise HTTPException(500, f"chat loop error: {e}") from e

    return {"success": True, "message": msg}


# ---------------------------------------------------------------------------
# Memory bank listing (other memory endpoints live in routers/memory.py)
# ---------------------------------------------------------------------------


@router.get("/api/memory/banks")
async def api_get_memory_banks():
    return {"banks": await get_memory_banks()}
