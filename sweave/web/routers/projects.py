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
    update_permission_roots,
)
from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


# ---------------------------------------------------------------------------
# R4.1 step 1b: WS event vocabulary for the foundation nav.
#
# The live project/session tree in the sidebar needs WS events to
# refresh without polling. The router is the publish boundary
# (it's the only layer with ``state`` in scope; the service layer
# stays pure). Five events, unified names, no legacy aliases (the
# v1 vanilla UI is retired).
#
#   project.created       {name, path}
#   project.deleted       {name}
#   session.created       {id, name, project_name}
#   session.deleted       {id, project_name}
#   active_session.changed {id, project_name}
#
# The data payloads are minimal -- the consumer refetches the
# list/detail. The events just say "something changed, invalidate".
# ---------------------------------------------------------------------------


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
    # M1.12: user-declared permission roots (human-declared only,
    # ruling 2026-09-10). Omitted = empty (default roots only).
    permission_roots: Optional[list[str]] = None


class SessionCreateRequest(BaseModel):
    name: str
    project_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.post("/api/projects")
async def api_create_project(
    request: ProjectCreateRequest,
    state: AppState = Depends(get_state),
):
    try:
            project = await create_project(
                ProjectCreate(
                    name=request.name,
                    path=request.path,
                    description=request.description,
                    worktree_base=request.worktree_base,
                    permission_roots=request.permission_roots,
                )
            )
    except Exception as e:
        raise HTTPException(400, str(e))
    # R4.1: notify subscribers (the foundation nav refetches
    # the project list on this event; no payload refetch
    # needed -- ``name`` + ``path`` are enough to render the
    # new entry if the consumer chose to).
    await state.publish(
        "project.created",
        {"name": project["name"], "path": project["path"]},
    )
    return {"success": True, "project": project}


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


class PermissionRootsRequest(BaseModel):
    roots: list[str]


@router.put("/api/projects/{name}/permission_roots")
async def api_update_permission_roots(
    name: str,
    request: PermissionRootsRequest,
    state: AppState = Depends(get_state),
):
    """Replace the project's user-declared permission roots (M1.12).
    Human-declared only (ruling 2026-09-10); specialists never
    nominate roots."""
    try:
        result = await update_permission_roots(name, request.roots)
    except ValueError as e:
        raise HTTPException(404, str(e))
    await state.publish(
        "project.updated", {"name": name}
    )
    return result


@router.delete("/api/projects/{name}")
async def api_delete_project(
    name: str,
    state: AppState = Depends(get_state),
):
    result = await delete_project(name)
    # R4.1: the session list for the deleted project also
    # becomes invalid. The consumer's React Query tree handles
    # the cascade (it refetches on session.created/deleted too),
    # so a single event is enough.
    await state.publish("project.deleted", {"name": name})
    return result


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.post("/api/sessions")
async def api_create_session(
    request: SessionCreateRequest,
    state: AppState = Depends(get_state),
):
    try:
        session = await create_session(
            SessionCreate(name=request.name, project_name=request.project_name)
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    # R4.1: the session tree refreshes on this event. The
    # payload carries the project_name so consumers can
    # decide whether to invalidate their own list (the
    # sidebar only shows the active project's tree).
    await state.publish(
        "session.created",
        {
            "id": session["id"],
            "name": session["name"],
            "project_name": session["project_name"],
        },
    )
    return {"success": True, "session": session}


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
async def api_set_active_session(
    session_id: str,
    state: AppState = Depends(get_state),
):
    try:
        result = await set_active_session(session_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    # R4.1: the active session pill + the topbar statusline
    # need to refresh without a page reload. The full
    # session id is enough (the consumer's session query
    # already caches the details).
    active = await get_active_session()
    await state.publish(
        "active_session.changed",
        {
            "id": session_id,
            "project_name": active["project_name"] if active else None,
        },
    )
    return result


@router.delete("/api/sessions/{session_id}")
async def api_delete_session(
    session_id: str,
    state: AppState = Depends(get_state),
):
    # R4.1: resolve the project_name BEFORE the delete -- the
    # post-delete get_session would 404, and the foundation nav
    # uses the project_name to decide whether the session was
    # in the active project (and therefore the user's tree
    # needs an invalidation).
    session = await get_session(session_id)
    project_name = session["project_name"] if session else None
    result = await delete_session(session_id)
    await state.publish(
        "session.deleted",
        {"id": session_id, "project_name": project_name},
    )
    return result


@router.post("/api/sessions/{session_id}/messages")
async def api_add_message(
    session_id: str,
    message: MessageCreate,
    state: AppState = Depends(get_state),
):
    # M1.7 step 2: a user message drives the orchestrator chat loop.
    # M1.8 fix (2026-09-08): when the chat loop owns the turn, the LOOP
    # persists the user message + emits the single ``message.added``
    # (its ``_run_turn_body`` step 1). The route used to persist first,
    # so every user turn recorded TWO user messages (one POST -> two
    # user rows on disk + two WS events; the UI then showed duplicated
    # user bubbles). Non-user roles (system, tool, assistant) keep the
    # legacy persist-only contract.
    if message.role == "user" and state.chat_loop is not None:
        try:
            assistant_msg = await state.chat_loop.run_turn(
                session_id=session_id,
                user_content=message.content,
            )
            return {
                "success": True,
                "assistant": assistant_msg,
            }
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001
            # The loop is best-effort from the router's perspective:
            # if the orchestrator is unreachable, the loop itself
            # persists an explicit assistant error message before this
            # fires. Surface catastrophic failures (e.g. session not
            # found) as a 500.
            raise HTTPException(500, f"chat loop error: {e}") from e

    try:
        msg = await add_message(session_id, message)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except TypeError as e:
        raise HTTPException(400, str(e))

    return {"success": True, "message": msg}


class RerunRequest(BaseModel):
    from_message_id: str
    # New user content (edit). Omitted/null = pure retry of the same text.
    content: Optional[str] = None


@router.post("/api/sessions/{session_id}/rerun")
async def api_rerun_turn(
    session_id: str,
    request: RerunRequest,
    state: AppState = Depends(get_state),
):
    """Re-run the turn starting at a past user message (edit + resend /
    retry). Later messages are flagged superseded (record, not
    deletion); an edit rotates the orchestrator session binding while
    a pure retry keeps it. Returns the new assistant message."""
    if state.chat_loop is None:
        raise HTTPException(500, "chat loop unavailable")
    try:
        assistant_msg = await state.chat_loop.rerun_turn(
            session_id=session_id,
            from_message_id=request.from_message_id,
            content=request.content,
        )
        return {"success": True, "assistant": assistant_msg}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except TypeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"chat loop error: {e}") from e


# ---------------------------------------------------------------------------
# Memory bank listing (other memory endpoints live in routers/memory.py)
# ---------------------------------------------------------------------------


@router.get("/api/memory/banks")
async def api_get_memory_banks():
    return {"banks": await get_memory_banks()}
