from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sweave.runtime.locking import atomic_write_json_sync


@dataclass
class Message:
    """A single message in a session conversation."""
    id: str
    role: str  # "user", "assistant", "system", "tool"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    agent: str | None = None  # Which agent produced this (for assistant messages)
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "agent": self.agent,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            id=data["id"],
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            agent=data.get("agent"),
            tool_name=data.get("tool_name"),
            tool_args=data.get("tool_args"),
            tool_result=data.get("tool_result"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ChildSession:
    """A specialist agent session spawned from a parent session.

    The ``delegation_id`` field (M1.1) is the M1.prep-era link to the
    runtime :class:`~sweave.runtime.delegation_store.Delegation` record.
    The Children tab in the UI v1 render path reads this field to
    jump from a child entry to the corresponding delegation's full
    trace / manifest. Bridge writes happen in
    :meth:`sweave.runtime.job_runner.JobRunner._run` — a new child
    entry is added to the parent session the moment a delegation is
    submitted. Pre-M1.1 child entries have ``delegation_id=None`` and
    the UI falls back to the v1 fields.

    R4 removes the bridge entirely when the Children tab reads
    delegations directly.
    """
    id: str
    parent_session_id: str
    agent_name: str
    task: str
    worktree_path: Path | None = None
    status: str = "running"  # "running", "completed", "failed"
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    output: str = ""
    error: str | None = None
    # M1.1: link to the runtime Delegation record (None for pre-M1.1
    # children; the UI v1 render path treats None as "legacy entry").
    delegation_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_session_id": self.parent_session_id,
            "agent_name": self.agent_name,
            "task": self.task,
            "worktree_path": str(self.worktree_path) if self.worktree_path else None,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "output": self.output,
            "error": self.error,
            "delegation_id": self.delegation_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChildSession":
        return cls(
            id=data["id"],
            parent_session_id=data["parent_session_id"],
            agent_name=data["agent_name"],
            task=data["task"],
            worktree_path=Path(data["worktree_path"]) if data.get("worktree_path") else None,
            status=data.get("status", "running"),
            created_at=datetime.fromisoformat(data["created_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            output=data.get("output", ""),
            error=data.get("error"),
            # M1.1: optional on read; legacy JSON files predate the field.
            delegation_id=data.get("delegation_id"),
        )


@dataclass
class Session:
    """An orchestrated conversation within a project.

    A session can spawn child sessions (specialist agent runs).
    Each session has its own message history and context.
    """
    id: str
    project_name: str
    name: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Session state
    status: str = "active"  # "active", "paused", "completed"
    current_agent: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    # Messages and children
    messages: list[Message] = field(default_factory=list)
    children: list[ChildSession] = field(default_factory=list)

    # Memory - scoped to session
    memory_bank: str = ""

    # M1.7 step 1: per-Session orchestrator binding.
    # schema_version is bumped to 2 in step 4 once the
    # last_memory_recall_ts / last_git_snapshot fields land. For now
    # legacy session files (no schema_version) load as v1 with the
    # field defaults below.
    schema_version: int = 1
    # The orchestrator's durable opencode session id (M1.7 step 1;
    # replaces the per-Project Specialist.session_id binding that
    # M1.3 used). One orchestrator conversation per (project, session)
    # -- three Sweave sessions get three independent orchestrator
    # contexts.
    orchestrator_session_id: str | None = None
    # M1.7 step 4: the multi-source "what's new" delta. The runtime
    # uses these to compute the per-turn "what's new" section of the
    # composed prompt: memory entries with ts > last_memory_recall_ts,
    # plus git diff since last_git_snapshot. Set on first recall /
    # first snapshot; updated on each subsequent turn. None means
    # "no prior turn" (the runtime initialises last_memory_recall_ts
    # to "now" on the first turn of a new session -- the user
    # doesn't want a flood of historical entries as their first view).
    last_memory_recall_ts: datetime | None = None
    last_git_snapshot: str | None = None

    def __post_init__(self):
        if not self.memory_bank:
            self.memory_bank = f"session-{self.id}"

    def add_message(self, role: str, content: str, **kwargs) -> Message:
        msg = Message(
            id=str(uuid.uuid4())[:8],
            role=role,
            content=content,
            **kwargs,
        )
        self.messages.append(msg)
        self.updated_at = datetime.now()
        return msg

    def add_child(self, agent_name: str, task: str, worktree_path: Path | None = None) -> ChildSession:
        child = ChildSession(
            id=str(uuid.uuid4())[:8],
            parent_session_id=self.id,
            agent_name=agent_name,
            task=task,
            worktree_path=worktree_path,
        )
        self.children.append(child)
        self.updated_at = datetime.now()
        return child

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_name": self.project_name,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status,
            "current_agent": self.current_agent,
            "context": self.context,
            "messages": [m.to_dict() for m in self.messages],
            "children": [c.to_dict() for c in self.children],
            "memory_bank": self.memory_bank,
            "schema_version": self.schema_version,
            "orchestrator_session_id": self.orchestrator_session_id,
            "last_memory_recall_ts": (
                self.last_memory_recall_ts.isoformat()
                if self.last_memory_recall_ts is not None
                else None
            ),
            "last_git_snapshot": self.last_git_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        # M1.7: legacy session files (pre-M1.7) lack schema_version /
        # orchestrator_session_id / last_memory_recall_ts /
        # last_git_snapshot; the dataclass defaults apply.
        last_recall_raw = data.get("last_memory_recall_ts")
        last_recall = (
            datetime.fromisoformat(last_recall_raw)
            if isinstance(last_recall_raw, str)
            else last_recall_raw
        )
        session = cls(
            id=data["id"],
            project_name=data["project_name"],
            name=data["name"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            status=data.get("status", "active"),
            current_agent=data.get("current_agent"),
            context=data.get("context", {}),
            memory_bank=data.get("memory_bank", ""),
            schema_version=data.get("schema_version", 1),
            orchestrator_session_id=data.get("orchestrator_session_id"),
            last_memory_recall_ts=last_recall,
            last_git_snapshot=data.get("last_git_snapshot"),
        )
        session.messages = [Message.from_dict(m) for m in data.get("messages", [])]
        session.children = [ChildSession.from_dict(c) for c in data.get("children", [])]
        return session


def _migrate_default_harness(value: object) -> str:
    """Normalise the display-only project harness label.

    Missing or legacy ``"opencode"`` values become ``"sweave-engine"``
    (the post-step-4 default); any other stored value passes through
    untouched. Safe: nothing reads this field for selection.
    """
    if not value or value == "opencode":
        return "sweave-engine"
    return str(value)


@dataclass
class Project:
    """A project = a folder on disk with its own config, memory, and worktrees."""
    name: str
    path: Path
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Project-specific settings
    # Display-only project fallback label (2026-09-14: NOTHING reads
    # this for selection — per-specialist harness wins, then the
    # operator config default. Default + legacy-"opencode" migrate to
    # sweave-engine so the Settings row stops contradicting reality).
    default_harness: str = "sweave-engine"
    memory_bank: str = ""  # Auto-set to "project-{name}" if empty

    # Model overrides for this project
    model_overrides: dict[str, str] = field(default_factory=dict)

    # Routing rules specific to this project
    routing_rules: list[dict[str, str]] = field(default_factory=list)

    # Project-scoped agents (in addition to global)
    agents: list[dict[str, Any]] = field(default_factory=list)

    # M1.9 step 2: per-project worktree base path. Overrides the
    # global ``config.git.worktree_base`` for this project. ``None``
    # means "use the global default" -- the legacy behaviour. The
    # scratch-project convention: the dev repo is never its own
    # live-gate target; a scratch project always sets
    # ``worktree_base`` to a temp dir.
    worktree_base: str | None = None

    # M1.12: user-declared permission roots (ruling 2026-09-10:
    # human-declared only -- specialists never nominate). Outside-cwd
    # paths under these roots resolve to "allow" in the rendered
    # opencode permission block (in addition to the built-in roots:
    # cwd subfolders, the project's worktrees, and ~/.sweave). Paths
    # NOT under any root (and not the cwd) ask the human via the
    # M1.12 blocking-question flow, never a silent allow.
    permission_roots: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.memory_bank:
            self.memory_bank = f"project-{self.name}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "default_harness": self.default_harness,
            "memory_bank": self.memory_bank,
            "model_overrides": self.model_overrides,
            "routing_rules": self.routing_rules,
            "agents": self.agents,
            "worktree_base": self.worktree_base,
            "permission_roots": self.permission_roots,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        project = cls(
            name=data["name"],
            path=Path(data["path"]),
            description=data.get("description", ""),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            default_harness=_migrate_default_harness(data.get("default_harness")),
            memory_bank=data.get("memory_bank", ""),
            model_overrides=data.get("model_overrides", {}),
            routing_rules=data.get("routing_rules", []),
            agents=data.get("agents", []),
            # M1.9 step 2: legacy project files (pre-M1.9) have no
            # ``worktree_base`` field -- the global default wins.
            worktree_base=data.get("worktree_base"),
            # M1.12: legacy project files (pre-M1.12) have no
            # ``permission_roots`` -- the empty list is the legacy
            # behaviour (default roots only).
            permission_roots=list(data.get("permission_roots", [])),
        )
        return project


class ProjectManager:
    """Manages projects and sessions."""

    def __init__(self, base_path: Path = None):
        self.base_path = base_path or Path.home() / ".sweave"
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.projects_dir = self.base_path / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

        self.global_config_path = self.base_path / "config.json"
        self._projects: dict[str, Project] = {}
        self._sessions: dict[str, Session] = {}
        self._active_project: str | None = None
        self._active_session: str | None = None
        # Per-project write locks. Created lazily. threading.Lock (not asyncio)
        # because ProjectManager is currently sync; the lock is the cheapest
        # thing that serialises concurrent JSON writes for the same project.
        # M1.6 (delegation/deferral async paths) may introduce a parallel
        # asyncio.Lock set on the AppState.
        self._project_locks: dict[str, threading.Lock] = {}
        self._project_locks_meta = threading.Lock()

    def load(self):
        """Load projects and sessions from disk.

        Skips stray ``*.tmp`` files (left behind if a write was interrupted
        before the atomic rename). Malformed project/session JSON is logged
        and skipped so one bad file does not poison the whole load.
        """
        # Load global config
        if self.global_config_path.exists():
            try:
                # UTF-8: writes go through atomic_write_json_sync
                # (utf-8); the read side must match or any non-ASCII
                # content (e.g. an emoji in a chat message) breaks
                # the load on locale-default (cp1252) systems.
                with open(self.global_config_path, encoding="utf-8") as f:
                    global_config = json.load(f)
                self._active_project = global_config.get("active_project")
                self._active_session = global_config.get("active_session")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                # Corrupt global config shouldn't kill the server; the
                # active-project pointers will be None and the user can
                # re-set them via /api/projects/{name}/active.
                self._active_project = None
                self._active_session = None

        # Load projects
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            config_file = project_dir / "project.json"
            if not config_file.exists():
                continue
            try:
                with open(config_file, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            try:
                project = Project.from_dict(data)
            except (KeyError, ValueError):
                continue
            self._projects[project.name] = project

            # Load sessions for this project
            sessions_dir = project_dir / "sessions"
            if not sessions_dir.exists():
                continue
            for session_file in sessions_dir.glob("*.json"):
                # Skip tmp files left by an interrupted atomic_write_json.
                if session_file.name.endswith(".tmp"):
                    continue
                try:
                    with open(session_file, encoding="utf-8") as f:
                        session_data = json.load(f)
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    continue
                try:
                    session = Session.from_dict(session_data)
                except (KeyError, ValueError):
                    continue
                self._sessions[session.id] = session

    def save_global(self):
        """Save global config (atomic)."""
        config = {
            "active_project": self._active_project,
            "active_session": self._active_session,
        }
        atomic_write_json_sync(self.global_config_path, config)

    def _lock_for(self, project_name: str) -> threading.Lock:
        """Return the per-project write lock, creating it lazily.

        The first call for *project_name* creates a fresh ``threading.Lock``;
        subsequent calls return the same lock. A meta-lock serialises the
        ``_project_locks`` dict itself so two concurrent first-callers can't
        race to insert.
        """
        existing = self._project_locks.get(project_name)
        if existing is not None:
            return existing
        with self._project_locks_meta:
            existing = self._project_locks.get(project_name)
            if existing is None:
                existing = threading.Lock()
                self._project_locks[project_name] = existing
            return existing

    def save_project(self, project: Project):
        """Save project config (atomic, per-project lock)."""
        project.updated_at = datetime.now()
        project_dir = self.projects_dir / project.name
        project_dir.mkdir(parents=True, exist_ok=True)
        config_file = project_dir / "project.json"
        with self._lock_for(project.name):
            atomic_write_json_sync(config_file, project.to_dict())

    def save_session(self, session: Session):
        """Save session (atomic, per-project lock)."""
        session.updated_at = datetime.now()
        project_dir = self.projects_dir / session.project_name
        sessions_dir = project_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_file = sessions_dir / f"{session.id}.json"
        with self._lock_for(session.project_name):
            atomic_write_json_sync(session_file, session.to_dict())

    # Project operations
    def create_project(self, name: str, path: Path, description: str = "") -> Project:
        """Create a new project from a folder path."""
        if name in self._projects:
            raise ValueError(f"Project '{name}' already exists")

        path = Path(path).resolve()
        if not path.exists():
            raise ValueError(f"Path '{path}' does not exist")
        if not path.is_dir():
            raise ValueError(f"Path '{path}' is not a directory")

        project = Project(
            name=name,
            path=path,
            description=description or f"Project at {path}",
        )
        self._projects[name] = project
        self.save_project(project)

        # Set as active if first project
        if self._active_project is None:
            self.set_active_project(name)

        return project

    def get_project(self, name: str) -> Project | None:
        return self._projects.get(name)

    def list_projects(self) -> list[Project]:
        return list(self._projects.values())

    def set_active_project(self, name: str):
        if name not in self._projects:
            raise ValueError(f"Project '{name}' not found")
        self._active_project = name
        self._active_session = None  # Reset session when switching project
        self.save_global()

    def get_active_project(self) -> Project | None:
        if self._active_project:
            return self._projects.get(self._active_project)
        return None

    def delete_project(self, name: str):
        if name not in self._projects:
            raise ValueError(f"Project '{name}' not found")

        # Delete project directory
        import shutil
        project_dir = self.projects_dir / name
        if project_dir.exists():
            shutil.rmtree(project_dir)

        # Also delete sessions from memory
        self._sessions = {
            sid: s for sid, s in self._sessions.items() if s.project_name != name
        }

        del self._projects[name]

        if self._active_project == name:
            self._active_project = None
            self.save_global()

    # Session operations
    def create_session(self, project_name: str, session_name: str = None) -> Session:
        project = self.get_project(project_name)
        if not project:
            raise ValueError(f"Project '{project_name}' not found")

        if not session_name:
            session_name = f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        session = Session(
            id=f"{project_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            project_name=project_name,
            name=session_name,
        )
        self._sessions[session.id] = session
        self.save_session(session)

        # Auto-set as active if first session
        if self._active_session is None or self.get_active_session() is None:
            self.set_active_session(session.id)

        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def rename_session(self, session_id: str, name: str) -> Session:
        """Rename a session (manual rename + auto-titling share it).

        Raises LookupError for unknown ids, ValueError for blank
        names. Names are capped at 120 chars (UI rows).
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise LookupError(f"Session '{session_id}' not found")
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("Session name must be non-empty")
        session.name = cleaned[:120]
        self.save_session(session)
        return session

    def list_sessions(self, project_name: str | None = None) -> list[Session]:
        if project_name:
            return [s for s in self._sessions.values() if s.project_name == project_name]
        return list(self._sessions.values())

    def set_active_session(self, session_id: str):
        if session_id not in self._sessions:
            raise ValueError(f"Session '{session_id}' not found")
        self._active_session = session_id
        self.save_global()

    def get_active_session(self) -> Session | None:
        if self._active_session:
            return self._sessions.get(self._active_session)
        return None

    def delete_session(self, session_id: str):
        if session_id not in self._sessions:
            raise ValueError(f"Session '{session_id}' not found")

        session = self._sessions[session_id]
        project_dir = self.projects_dir / session.project_name
        session_file = project_dir / "sessions" / f"{session_id}.json"
        if session_file.exists():
            session_file.unlink()

        del self._sessions[session_id]

        if self._active_session == session_id:
            self._active_session = None
            self.save_global()

    # Memory bank resolution - hierarchical
    def get_memory_bank(self, scope: str = "project") -> str:
        """Resolve memory bank based on scope and active project/session.

        Scopes:
          - "global": always returns "global"
          - "project": returns project's bank
          - "session": returns session's bank
        """
        if scope == "global":
            return "global"

        active_session = self.get_active_session()
        if scope == "session" and active_session:
            return active_session.memory_bank

        active_project = self.get_active_project()
        if active_project:
            return active_project.memory_bank

        return "global"

    def get_available_memory_banks(self) -> list[dict]:
        """Get all available memory banks for the current project/session context."""
        banks = [{"id": "global", "scope": "global", "name": "Global"}]

        active_project = self.get_active_project()
        if active_project:
            banks.append({
                "id": active_project.memory_bank,
                "scope": "project",
                "name": f"Project: {active_project.name}",
            })

        active_session = self.get_active_session()
        if active_session:
            banks.append({
                "id": active_session.memory_bank,
                "scope": "session",
                "name": f"Session: {active_session.name}",
            })

        return banks


# Global instance
project_manager = ProjectManager()