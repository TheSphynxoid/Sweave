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
    from sweave.runtime.delegation_manager import DelegationManager
    from sweave.runtime.job_runner import JobRunner
    from sweave.runtime.locking import ProjectLockRegistry
    from sweave.runtime.specialist_store import SpecialistResolver
    from sweave.tools import (
        DelegateTaskTool,
        MemoryTool,
        RouteTaskTool,
        WorktreeTool,
    )
    from sweave.workspace.manager import WorktreeManager

logger = logging.getLogger(__name__)


def _anchored_agents_path() -> Path:
    """The home-anchored path that replaces the M1.prep CWD-relative
    ``Path('agents.yaml')`` (amendment E). Created on first use by the
    AppState constructor; tests that point ``dynamic_agents_path`` at a
    tmp dir still work because the field overrides the default.
    """
    return Path.home() / ".sweave" / "agents.yaml"


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

    # Legacy dynamic-agents mechanism (M1.prep era). Kept as a *derived view*
    # for the existing routers and the rule-router until R4 folds them. The
    # source of truth is now the :class:`SpecialistResolver` (M1.2); the
    # ``dynamic_agents_path`` is the home-anchored ``~/.sweave/agents.yaml``
    # (amendment E -- kills the CWD-relative gotcha).
    dynamic_agents: dict[str, AgentSpec] = field(default_factory=dict)
    dynamic_agents_path: Path = field(default_factory=_anchored_agents_path)

    # WSEventBus is the single pub/sub for everything that wants to reach
    # connected WebSocket clients. Constructed in lifespan and assigned to
    # ``event_bus``; routers always read it from the state object.
    event_bus: Any = None  # type: ignore[assignment]
    job_runner: "JobRunner | None" = None  # set in lifespan
    # Per-project delegation store registry (M1.1 step 2). Lazy: no
    # project files are opened at startup; a store is constructed on
    # first delegation for that project. ``delegation_stores_for()`` is
    # the canonical accessor; the attribute may be ``None`` only
    # during lifespan setup (before the lifespan hook runs).
    delegation_stores: Any = None  # type: ignore[assignment]
    # Ephemeral sub-agent run store (M1.1 step 3). Per-process, in-memory
    # only, capped at MAX_RUNS. R2's /investigate will populate it; M1.1
    # only delivers the type + store + lifecycle primitives (the API
    # endpoints arrive in step 4).
    subagent_runs: Any = None  # type: ignore[assignment]
    # Specialist resolver (M1.2). Lazy: constructed in lifespan; the
    # routers in step 3 use it for /api/specialists CRUD. May be ``None``
    # before lifespan (e.g. in tests that build the AppState directly).
    specialist_resolver: Any = None  # type: ignore[assignment]
    # Trace log directory (M1.4+M1.5 step 3). Set in lifespan so the
    # ``POST /api/delegations/{id}/promote`` endpoint can write the
    # ``status_changed`` event without rebuilding the path. Default is
    # the same ``~/.sweave/traces`` the runner uses; tests may point
    # this at a temp dir.
    traces_dir: Path = field(default_factory=lambda: Path.home() / ".sweave" / "traces")
    # M1.6: per-process DelegationManager (depth / loop / budget).
    # Built once in lifespan from the loaded config; the v2 task
    # endpoint runs ``validate`` before delegating to the runner.
    delegation_manager: Any = None  # type: ignore[assignment]
    # M1.7: ChatLoop (orchestrator conversation driver). Built in
    # lifespan after the SpecialistRuntime + specialist_resolver are
    # wired. The chat endpoint calls ``run_turn`` for every user
    # message; the loop is responsible for the per-session serial
    # queue, the Session-bound orchestrator binding (M1.7 step 1),
    # and persisting the assistant reply.
    chat_loop: Any = None  # type: ignore[assignment]

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
        )

    async def load_dynamic_agents(self) -> None:
        """Load dynamic agents from the anchored ``agents.yaml`` (if present).

        The path is now home-anchored (M1.2 amendment E); the file is
        also the same file the :class:`SpecialistResolver`'s global store
        reads/writes. ``load_dynamic_agents`` populates the in-memory
        ``dynamic_agents`` dict as a *derived view* for the legacy
        router; the source of truth is the resolver.
        """
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
        """Persist dynamic agents back to the anchored ``agents.yaml`` (atomic).

        Same file the resolver's global store writes. Writes go through
        ``atomic_write_json`` (yaml mode) so the file is replaced atomically.
        """
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

    def ensure_specialist_resolver(self) -> "SpecialistResolver":
        """Return the resolver, constructing it on first call.

        The resolver uses the same ``dynamic_agents_path`` as the legacy
        view, so writes through the resolver and reads through
        ``load_dynamic_agents`` see the same file.
        """
        if self.specialist_resolver is None:
            from sweave.runtime.specialist_store import SpecialistResolver

            self.specialist_resolver = SpecialistResolver()
        return self.specialist_resolver

    async def bootstrap_specialists(self) -> None:
        """One-time import of legacy dynamic agents into the new resolver.

        Triggered from lifespan after the dynamic_agents file is loaded.
        The import is **one-way**: the new store is the source of truth
        going forward. We only import when the *new* global store file
        (anchored path) is absent, so a user who already has a populated
        file isn't re-imported on every restart.

        The legacy file at the same anchored path is the same physical
        file the resolver reads; we only do a shape conversion (AgentSpec
        list -> Specialist list) and a one-time write if the resolver's
        global store has never been touched.
        """
        resolver = self.ensure_specialist_resolver()
        # The resolver already loaded the file (or created an empty store)
        # on construction. We only need to import if the anchored file
        # is absent AND the legacy dynamic_agents dict has entries.
        anchored = Path.home() / ".sweave" / "agents.yaml"
        if anchored.exists():
            return  # file present, no legacy import needed
        if not self.dynamic_agents:
            return
        # Map each AgentSpec to a Specialist (global scope, role_ref=None
        # -- the legacy spec doesn't carry a role hint; the model field
        # becomes current_model).
        for spec in self.dynamic_agents.values():
            from sweave.runtime.specialist_store import Specialist

            try:
                rec = Specialist(
                    name=spec.name,
                    scope="global",
                    is_orchestrator=False,
                    role_ref=None,
                    description="",
                    system_prompt=spec.system_prompt,
                    harness=spec.harness or "opencode",
                    current_model=spec.model or None,
                )
                resolver.create(rec)
                logger.info("Imported legacy dynamic agent %s -> global", spec.name)
            except ValueError as e:
                logger.warning("Skipped legacy agent %s: %s", spec.name, e)

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

    async def delegation_stores_for(self, project_dir: Path) -> Any:
        """Return the :class:`DelegationStore` for *project_dir*.

        Lazy-creates on first access. If the registry isn't yet
        initialised (e.g. during lifespan setup), returns a fresh
        in-memory :class:`PerProjectDelegationStores` and assigns it
        so subsequent calls share the same registry.
        """
        if self.delegation_stores is None:
            from sweave.runtime.delegation_store import PerProjectDelegationStores

            self.delegation_stores = PerProjectDelegationStores()
        return await self.delegation_stores.for_project(project_dir)

    def active_project_name(self) -> str | None:
        """Return the active project name from the singleton
        :class:`ProjectManager`, or ``None`` if no project is active.

        Used by the routers as a default for the v2 task's
        ``project_name`` field so a delegation filed without an
        explicit project pin still lands in the right per-project
        store.
        """
        from sweave.projects import project_manager

        active = project_manager.get_active_project()
        return active.name if active is not None else None
