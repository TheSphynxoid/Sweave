"""Application state for the Sweave web server.

All long-lived service objects live on :class:`AppState`, which is constructed
in the FastAPI ``lifespan`` hook. Routers and dependencies read from
``request.app.state.app_state`` via :mod:`sweave.web.deps`.

This replaces the import-time module-level singletons that the server used
to create. The previous design had hidden global state and made the server
impossible to instantiate twice in one process (e.g. for tests).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sweave.config.manager import ConfigManager
from sweave.config.schemas import AgentSpec

if TYPE_CHECKING:
    from sweave.memory.backends import MemoryFactory  # noqa: F401
    from sweave.router.router import RuleRouter
    from sweave.runtime.job_runner import JobRunner
    from sweave.runtime.locking import ProjectLockRegistry
    from sweave.tools import (
        DelegateTaskTool,
        MemoryTool,
        RouteTaskTool,
        WorktreeTool,
    )
    from sweave.workspace.manager import WorktreeManager

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    """Container for all long-lived service objects.

    Construct once in the FastAPI ``lifespan`` context manager; never mutate
    the type or identity of these fields after construction. Routers obtain
    the state via :func:`sweave.web.deps.get_state`.
    """

    config_manager: ConfigManager
    router: "RuleRouter"
    worktree_manager: "WorktreeManager"
    delegate_tool: "DelegateTaskTool"
    route_tool: "RouteTaskTool"
    worktree_tool: "WorktreeTool"
    memory_tool: "MemoryTool"

    project_locks: "ProjectLockRegistry"

    dynamic_agents: dict[str, AgentSpec] = field(default_factory=dict)
    dynamic_agents_path: Path = field(default_factory=lambda: Path("agents.yaml"))

    # WSEventBus is the single pub/sub for everything that wants to reach
    # connected WebSocket clients. Constructed in lifespan and assigned to
    # ``event_bus``; routers always read it from the state object.
    event_bus: Any = None  # type: ignore[assignment]
    job_runner: "JobRunner | None" = None  # set in lifespan
    delegation_store: Any = None  # set in lifespan (DelegationStore)

    @classmethod
    def build(cls, config_manager: ConfigManager) -> "AppState":
        """Construct an :class:`AppState` from a loaded config manager.

        Tools are constructed here so all the wiring is in one place; the
        lifespan context just calls this and attaches the result.
        """
        # Imported lazily so the type-only imports above don't trigger a cycle.
        from sweave.router.router import RuleRouter
        from sweave.runtime.locking import ProjectLockRegistry
        from sweave.tools import (
            DelegateTaskTool,
            MemoryTool,
            RouteTaskTool,
            WorktreeTool,
        )
        from sweave.workspace.manager import WorktreeManager

        config = config_manager.get()
        worktree_manager = WorktreeManager(config.git.worktree_base)

        return cls(
            config_manager=config_manager,
            router=RuleRouter(config_manager),
            worktree_manager=worktree_manager,
            delegate_tool=DelegateTaskTool(config_manager, worktree_manager),
            route_tool=RouteTaskTool(config_manager),
            worktree_tool=WorktreeTool(worktree_manager),
            memory_tool=MemoryTool(config_manager),
            project_locks=ProjectLockRegistry(),
            dynamic_agents_path=Path("agents.yaml"),
        )

    async def load_dynamic_agents(self) -> None:
        """Load dynamic agents from ``agents.yaml`` (if present)."""
        from dataclasses import asdict
        import yaml

        if not self.dynamic_agents_path.exists():
            return
        try:
            data = yaml.safe_load(self.dynamic_agents_path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to load %s: %s", self.dynamic_agents_path, e)
            return
        for agent_data in data.get("agents", []):
            try:
                spec = AgentSpec(**agent_data)
            except TypeError as e:
                logger.warning("Skipping bad agent spec: %s", e)
                continue
            self.dynamic_agents[spec.name] = spec

    async def save_dynamic_agents(self) -> None:
        """Persist dynamic agents back to ``agents.yaml`` (atomic write)."""
        from dataclasses import asdict
        import yaml

        from sweave.runtime.locking import atomic_write_json

        agents_data = []
        for spec in self.dynamic_agents.values():
            d = asdict(spec)
            d["worktree_path"] = str(d["worktree_path"])
            agents_data.append(d)
        await atomic_write_json(
            self.dynamic_agents_path, {"agents": agents_data}, use_yaml=True
        )

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        """Publish a WebSocket event through the event bus.

        No-op if the bus is not yet constructed (e.g. tests that build an
        ``AppState`` without going through :func:`build` + lifespan). All
        routers SHOULD call this instead of touching the bus directly so
        the fallback behaviour stays in one place.
        """
        if self.event_bus is None:
            return
        await self.event_bus.publish(event, data)
