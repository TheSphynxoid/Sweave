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
    # M1.9 step 2: per-project worktree_base override.
    worktree_base: str | None = None
    # M1.12: user-declared permission roots (human-declared only).
    permission_roots: list[str] | None = None


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
        # M1.9 step 2: per-project worktree_base override. Set after
        # the create (which fills the dataclass from the manager's
        # defaults); ``None`` is the legacy behaviour.
        if request.worktree_base:
            project.worktree_base = request.worktree_base
            project_manager.save_project(project)
        # M1.12: user-declared permission roots (default None keeps
        # the legacy empty-list behaviour).
        if request.permission_roots:
            project.permission_roots = list(request.permission_roots)
            project_manager.save_project(project)
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
            # M1.12: the project's user-declared permission roots
            # feed the scoped ``external_directory`` render (plus the
            # built-in roots: cwd subfolders, worktrees, ~/.sweave).
            ensure_mcp_config(
                Path(project.path),
                permission_roots=list(project.permission_roots),
            )
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


async def update_permission_roots(name: str, roots: list[str]) -> dict:
    """Replace the project's user-declared permission roots (M1.12).

    Ruling 2026-09-10: human-declared only. After persisting, the
    per-project opencode.json is re-rendered (best-effort) so the
    next serve boot / activation sees the new scoped roots; already
    running serves pick the change up on their next restart.
    """
    project = project_manager.get_project(name)
    if project is None:
        raise ValueError(f"Project '{name}' not found")
    project.permission_roots = [str(r) for r in roots]
    project_manager.save_project(project)
    try:
        from pathlib import Path

        from sweave.runtime.mcp_config import ensure_mcp_config

        ensure_mcp_config(
            Path(project.path),
            permission_roots=list(project.permission_roots),
        )
    except Exception:  # noqa: BLE001
        pass
    return {"success": True, "permission_roots": project.permission_roots}


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


def _session_summary(s) -> dict:
    """Meta-only summary row (the SessionSummary wire shape)."""
    return {
        "id": s.id,
        "name": s.name,
        "project_name": s.project_name,
        "status": s.status,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "message_count": s.message_count,
        "child_count": len(s.children),
        "memory_bank": s.memory_bank,
    }


async def list_sessions(project_name: str | None = None) -> list[dict]:
    """List session METAS for a project (never faults transcripts)."""
    sessions = project_manager.list_sessions(project_name)
    active = project_manager.get_active_session()
    active_id = active.id if active else None
    return [
        {**_session_summary(s), "active": s.id == active_id}
        for s in sessions
    ]


async def get_session(session_id: str) -> dict | None:
    """Get full session details including messages.

    The one transcript-faulting read: opening a session pulls its
    jsonl. List/active paths stay meta-only.
    """
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


async def rename_session(session_id: str, name: str) -> dict:
    """Rename a session (manual rename + auto-titling share it)."""
    session = project_manager.rename_session(session_id, name)
    return session.to_dict()


async def add_message(session_id: str, message: MessageCreate) -> dict:
    """Append a message to a session (O(1) jsonl append + meta bump)."""
    # append_message raises ValueError for unknown ids (same contract
    # the old get_session-None path raised).
    msg = project_manager.append_message(
        session_id,
        role=message.role,
        content=message.content,
        agent=message.agent,
        tool_name=message.tool_name,
        tool_args=message.tool_args,
        tool_result=message.tool_result,
    )
    return msg.to_dict()


async def get_active_session() -> dict | None:
    """Active session as a SUMMARY (meta-only, never faults bodies).

    The UI types this as SessionSummary and never reads messages
    off it; the thread loads transcripts via get_session.
    """
    session = project_manager.get_active_session()
    if session:
        return _session_summary(session)
    return None


async def get_active_project() -> dict | None:
    project = project_manager.get_active_project()
    if project:
        return project.to_dict()
    return None


async def get_memory_banks() -> list[dict]:
    """Get available memory banks for the current context."""
    return project_manager.get_available_memory_banks()