from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import asyncio
import uuid

from sweave.projects import ProjectManager, Project, Session, project_manager


@dataclass
class ProjectCreate:
    name: str
    path: str
    description: str = ""


@dataclass
class SessionCreate:
    name: str
    project_name: str | None = None


@dataclass
class MessageCreate:
    role: str
    content: str
    agent: str | None = None
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None


@dataclass
class ChildSessionCreate:
    agent_name: str
    task: str


async def create_project(request: ProjectCreate) -> dict:
    """Create a new project from a folder path."""
    try:
        project = project_manager.create_project(
            name=request.name,
            path=Path(request.path),
            description=request.description,
        )
        return project.to_dict()
    except ValueError as e:
        raise e


async def list_projects() -> list[dict]:
    """List all projects."""
    projects = project_manager.list_projects()
    active = project_manager.get_active_project()
    return [
        {
            "name": p.name,
            "path": str(p.path),
            "description": p.description,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
            "memory_bank": p.memory_bank,
            "active": active and active.name == p.name,
        }
        for p in projects
    ]


async def get_project(name: str) -> dict | None:
    """Get project by name."""
    project = project_manager.get_project(name)
    if not project:
        return None
    return project.to_dict()


async def set_active_project(name: str) -> dict:
    """Set active project.

    Side effect (M1.6 step 3): the per-project ``opencode.json`` is
    updated to register the sweave MCP server (idempotently; user-
    written blocks are left alone). The orchestrator's opencode
    serve launched in this project's cwd then sees the MCP server
    and exposes ``defer`` + ``list_specialists`` to the model.
    """
    try:
        project_manager.set_active_project(name)
    except ValueError as e:
        raise e

    # M1.6: write (or refresh) the per-project opencode.json with
    # the sweave MCP block. Best-effort; a write failure doesn't
    # block the activate (the project is still active, the MCP
    # just won't be available until the next activation round).
    try:
        from pathlib import Path

        from sweave.runtime.mcp_config import ensure_mcp_config

        project = project_manager.get_project(name)
        if project is not None:
            ensure_mcp_config(Path(project.path))
    except Exception:  # noqa: BLE001
        # Don't fail the activation on plumbing errors; the project
        # is still active and the orchestrator can still run (just
        # without the defer tool until the next re-activation).
        pass

    return {"success": True, "active_project": name}


async def delete_project(name: str) -> dict:
    """Delete a project."""
    project_manager.delete_project(name)
    return {"success": True}


async def create_session(request: SessionCreate) -> dict:
    """Create a new session."""
    project_name = request.project_name or (
        project_manager.get_active_project().name
        if project_manager.get_active_project() else None
    )
    if not project_name:
        raise ValueError("No active project. Specify project_name or set active project.")

    session = project_manager.create_session(
        project_name=project_name,
        session_name=request.name,
    )
    return session.to_dict()


async def list_sessions(project_name: str | None = None) -> list[dict]:
    """List sessions for a project."""
    sessions = project_manager.list_sessions(project_name)
    active = project_manager.get_active_session()
    return [
        {
            "id": s.id,
            "name": s.name,
            "project_name": s.project_name,
            "status": s.status,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
            "message_count": len(s.messages),
            "child_count": len(s.children),
            "memory_bank": s.memory_bank,
            "active": active and active.id == s.id,
        }
        for s in sessions
    ]


async def get_session(session_id: str) -> dict | None:
    """Get full session details including messages and children."""
    session = project_manager.get_session(session_id)
    if not session:
        return None
    return session.to_dict()


async def set_active_session(session_id: str) -> dict:
    """Set active session."""
    try:
        project_manager.set_active_session(session_id)
        return {"success": True, "active_session": session_id}
    except ValueError as e:
        raise e


async def delete_session(session_id: str) -> dict:
    """Delete a session."""
    project_manager.delete_session(session_id)
    return {"success": True}


async def add_message(session_id: str, message: MessageCreate) -> dict:
    """Add a message to a session."""
    session = project_manager.get_session(session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found")

    msg = session.add_message(
        role=message.role,
        content=message.content,
        agent=message.agent,
        tool_name=message.tool_name,
        tool_args=message.tool_args,
        tool_result=message.tool_result,
    )
    project_manager.save_session(session)
    return msg.to_dict()


async def get_active_session() -> dict | None:
    session = project_manager.get_active_session()
    if session:
        return session.to_dict()
    return None


async def get_active_project() -> dict | None:
    project = project_manager.get_active_project()
    if project:
        return project.to_dict()
    return None


async def get_memory_banks() -> list[dict]:
    """Get available memory banks for the current context."""
    return project_manager.get_available_memory_banks()