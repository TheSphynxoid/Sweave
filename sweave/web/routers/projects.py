"""Project + session + memory-banks routes."""

from __future__ import annotations

import logging
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
    rename_session,
    set_active_project,
    set_active_session,
    update_permission_roots,
)
from sweave.chat.loop import TurnActiveError
from sweave.web.deps import get_state
from sweave.web.state import AppState

logger = logging.getLogger(__name__)

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


class SessionRenameRequest(BaseModel):
    name: str


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
    # M1.13 cleanup cascade (ruling 2026-09-11, ARCHIVE-not-delete):
    # before the registry entry is removed, archive the project's
    # delegations in place (status + stats preserved) and persist its
    # aggregate into the archive index. Best-effort: a cascade failure
    # is logged and never blocks the delete itself.
    if state.delegation_stores is not None:
        try:
            from sweave.runtime.delegation_archive import (
                archive_project_scope,
            )

            archived = await archive_project_scope(
                state.delegation_stores, name,
                index=getattr(state, "archive_index", None),
            )
            if archived:
                logger.info(
                    "Project delete cascade: archived %d delegation(s) "
                    "of project '%s'",
                    archived, name,
                )
        except Exception as cascade_err:  # noqa: BLE001
            logger.warning(
                "Project delete cascade failed for '%s': %s",
                name, cascade_err,
            )
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


@router.get("/api/sessions/{session_id}/turn")
async def api_get_session_turn(
    session_id: str,
    state: AppState = Depends(get_state),
):
    # Client-refresh recovery: the freshly loaded page (or a WS
    # reconnect) calls this immediately after mounting the thread to
    # learn whether the server still has a turn running for this
    # session. ``turn`` is None when idle; when active it carries the
    # delegation join key, the phase (waiting | streaming | question)
    # and the already-streamed text, so the UI can restore the
    # waiting/streaming indicator + the partial bubble in one call.
    # No error on idle: the UI can call this unconditionally without
    # 404 handling.
    chat_loop = state.chat_loop
    snapshot = (
        chat_loop.active_turn_snapshot(session_id)
        if chat_loop is not None
        else None
    )
    return {"active": snapshot is not None, "turn": snapshot}


@router.post("/api/sessions/{session_id}/turn/cancel")
async def api_cancel_session_turn(
    session_id: str,
    state: AppState = Depends(get_state),
):
    """Stop the live turn for a session (Stop button).

    Cancels the whole subtree (live children first, then the parent
    turn) and persists the already-streamed partial text as a
    ``cancelled`` assistant bubble — the thread shows the stop
    instead of losing the turn. 404 when no turn is running.
    """
    if state.chat_loop is None:
        raise HTTPException(500, "chat loop unavailable")
    try:
        assistant_msg = await state.chat_loop.cancel_turn(session_id=session_id)
        return {"success": True, "assistant": assistant_msg}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"chat cancel error: {e}") from e


def _turn_active_409_err(e: Exception) -> HTTPException:
    """Map ChatLoop.TurnActiveError to HTTP 409 with the active turn's
    snapshot. The double-send is REJECTED (never silently queued): a
    queued POST would hang the client's HTTP request for up to
    ``turn_timeout`` and a refreshed client could never see it.
    """
    snapshot = dict(getattr(e, "snapshot", {}))
    return HTTPException(
        409,
        detail={
            "error": "turn_active",
            "message": str(e),
            "turn": snapshot,
        },
    )


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
    # M1.13 cleanup cascade (ruling 2026-09-11, ARCHIVE-not-delete):
    # before the session row is removed, archive its delegations in
    # place (status + stats preserved). Best-effort; a cascade
    # failure never blocks the delete itself.
    if state.delegation_stores is not None:
        try:
            from sweave.runtime.delegation_archive import (
                archive_session_scope,
            )

            archived = await archive_session_scope(
                state.delegation_stores,
                session_id,
                project_name=project_name,
            )
            if archived:
                logger.info(
                    "Session delete cascade: archived %d delegation(s) "
                    "of session '%s'",
                    archived, session_id,
                )
        except Exception as cascade_err:  # noqa: BLE001
            logger.warning(
                "Session delete cascade failed for '%s': %s",
                session_id, cascade_err,
            )
    result = await delete_session(session_id)
    await state.publish(
        "session.deleted",
        {"id": session_id, "project_name": project_name},
    )
    return result


@router.patch("/api/sessions/{session_id}")
async def api_rename_session(
    session_id: str,
    request: SessionRenameRequest,
    state: AppState = Depends(get_state),
):
    """Rename a session (manual rename + auto-titling share it).

    404 for unknown ids, 400 for blank names. Publishes
    ``session.renamed`` so open UIs refresh the name without reload.
    """
    try:
        session = await rename_session(session_id, request.name)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    await state.publish(
        "session.renamed",
        {
            "id": session["id"],
            "name": session["name"],
            "project_name": session["project_name"],
        },
    )
    return {"success": True, "session": session}


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
        except TurnActiveError as e:
            # Double-send guard: a turn is already running for this
            # session; the 409 payload carries the active turn's
            # snapshot so the UI can attach to it (no phantom
            # double-send).
            raise _turn_active_409_err(e) from e
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
    deletion); an edit appends a revision user message with fork
    linkage (the original prompt survives) while a pure retry reuses
    the target row; the session binding is always kept. Returns the
    new assistant message."""
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
    except TurnActiveError as e:
        raise _turn_active_409_err(e) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"chat loop error: {e}") from e


# ---------------------------------------------------------------------------
# Memory bank listing (other memory endpoints live in routers/memory.py)
# ---------------------------------------------------------------------------


@router.get("/api/memory/banks")
async def api_get_memory_banks():
    return {"banks": await get_memory_banks()}
