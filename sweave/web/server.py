"""Sweave web server entry point.

Top-level wiring only. Routes live in :mod:`sweave.web.routers`; the long-lived
service objects live on :class:`sweave.web.state.AppState` and are constructed
in the ``lifespan`` hook. This module owns:

* the FastAPI ``app`` instance (``build_app()``)
* the lifespan context (loads config, projects, dynamic agents)
* the SPA routes (index, /agents, /tasks, /worktrees, /memory, /settings)
* the static file mount
* the ``/ws`` WebSocket endpoint (broadcast on the unified event bus)

Route definitions were split out in M1.prep step 2; see ``sweave/web/routers``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import jinja2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from sweave.config.manager import ConfigManager
from sweave.projects import project_manager
from sweave.web.deps import get_state as _get_state  # re-exported below as get_state
from sweave.web.state import AppState

logger = logging.getLogger(__name__)

# Re-export the dependency for routers that import ``sweave.web.server.get_state``.
get_state = _get_state


def _add_child_to_session(child, project_manager) -> None:
    """UI v1 compat bridge: add *child* to its parent session and persist.

    The JobRunner calls this on every delegation submit. If the
    parent session can't be resolved (e.g. an orchestrator that
    hasn't created a session yet) the bridge write is a no-op —
    the delegation still stands on its own in the per-project store
    and the UI's R4 children-reads-delegations path will surface it.

    Defined at module level (rather than inside the lifespan) so the
    closure is reusable and easy to mock in tests.
    """
    parent = project_manager.get_session(child.parent_session_id)
    if parent is None:
        return
    # Re-attach the existing child if a re-submit happened (idempotency:
    # a duplicate bridge write shouldn't create a second ChildSession).
    for existing in parent.children:
        if existing.id == child.id:
            return
    parent.children.append(child)
    project_manager.save_session(parent)


# ============================================================================
# Jinja templates (kept for any server-rendered fallback pages)
# ============================================================================

_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader("sweave/web/templates"),
    autoescape=True,
    enable_async=False,
    cache_size=-1,
)


def render_template(template_name: str, context: dict) -> str:
    template = _jinja_env.get_template(template_name)
    return template.render(context)


# ============================================================================
# Lifespan: build AppState and attach to the app
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise services on startup, clean up on shutdown."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner
    from sweave.runtime.subagent_store import SubAgentRunStore
    from sweave.web.events import WSEventBus

    config_manager = ConfigManager()
    config_manager.load()
    project_manager.load()

    state = AppState.build(config_manager)
    state.event_bus = WSEventBus()
    state.delegation_stores = PerProjectDelegationStores()
    state.subagent_runs = SubAgentRunStore()
    # M1.2 step 2: build the specialist resolver first so the JobRunner
    # can be wired with the specialist factory + saver closures below.
    from sweave.runtime.specialist_store import SpecialistResolver

    state.specialist_resolver = SpecialistResolver()
    state.delegate_tool.specialist_resolver = state.specialist_resolver

    state.job_runner = JobRunner(
        delegate_tool=state.delegate_tool,
        # JobRunner uses the per-project store registry; it picks a store
        # for the project on each delegation. M1.1 step 2.
        delegation_stores=state.delegation_stores,
        event_bus=state.event_bus,
        # Resolve project_name -> project.path via the singleton
        # ProjectManager. Returns None for unknown names (delegation
        # falls through to the global store at ~/.sweave/).
        project_dir_resolver=lambda name: (
            project_manager.get_project(name).path
            if project_manager.get_project(name) is not None
            else None
        ),
        # UI v1 compat bridge (M1.1 step 4): the runner hands us a
        # ChildSession, we add it to the parent session and persist.
        # R4 removes the bridge; until then, the Children tab keeps
        # rendering without any UI change.
        child_session_adder=lambda child: _add_child_to_session(
            child, project_manager
        ),
        # M1.2: resolve an agent name to a Specialist record
        # (project -> global -> seed). None = unknown name (the runner
        # falls back to a transient Specialist).
        specialist_factory=lambda agent_name: (
            state.specialist_resolver.resolve(
                agent_name,
                project_dir=(
                    project_manager.get_project(
                        project_manager.get_active_project().name
                    ).path
                    if project_manager.get_active_project() is not None
                    else None
                ),
            )
        ),
        # M1.3 step 5: persist the Specialist record (including the
        # session_id the runtime set) back to its store after each
        # delegation. Best-effort; the store's own atomic write
        # contract applies.
        specialist_saver=lambda specialist, project_name: (
            state.specialist_resolver.update(specialist, project_dir=(
                project_manager.get_project(project_name).path
                if project_name and project_manager.get_project(project_name) is not None
                else None
            ))
        ),
        # M1.6 step 2: the DelegationManager is wired in so the
        # runner can notify it on terminal transitions. The v2 task
        # endpoint already calls ``validate`` directly; this hook is
        # the *release* side (frees the per-chain cache so a new defer
        # in the same chain can pick a target that was previously
        # busy).
        delegation_manager=state.delegation_manager,
    )
    # One-time legacy import: if the anchored file is absent but the
    # in-memory dynamic_agents dict has entries (from the legacy CWD-
    # relative file), bring them into the new global store.
    await state.bootstrap_specialists()
    await state.load_dynamic_agents()

    # M1.3 step 3: build the SpecialistRuntime and wire it into the
    # JobRunner. From here on, delegations submitted through
    # POST /api/v2/tasks go through the runtime path (per-specialist
    # ServeRunner + session lifecycle + ModelRef routing + worktree
    # preamble). The legacy delegate_tool path remains available when
    # specialist_runtime is None (e.g. tests that stub it out).
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    serve_registry = ServeRunnerRegistry(event_bus=state.event_bus)
    state.job_runner.specialist_runtime = SpecialistRuntime(
        runners=serve_registry,
        event_bus=state.event_bus,
    )

    # M1.6 step 2: build the DelegationManager from the loaded config.
    # The v2 task endpoint runs ``validate`` before delegating to the
    # runner when ``parent_task_id`` is set; the MCP ``defer`` tool
    # also goes through this gate (via /api/v2/tasks).
    from sweave.runtime.delegation_manager import DelegationManager

    state.delegation_manager = DelegationManager.from_config(config_manager.get())

    app.state.app_state = state
    logger.info(
        "AppState built; %d dynamic agents loaded; JobRunner ready "
        "(SpecialistRuntime wired)",
        len(state.dynamic_agents),
    )

    try:
        yield
    finally:
        # Close any open WS connections cleanly
        if state.event_bus is not None:
            for ws in list(state.event_bus._subscribers):  # noqa: SLF001
                try:
                    await ws.close()
                except Exception:
                    pass


def build_app() -> FastAPI:
    """Construct a fresh FastAPI app.

    Kept as a factory so tests can build an isolated app with stubbed state.
    Production code uses the module-level ``app`` (instantiated below) so
    that all ``@app.<verb>(...)`` decorators in this file register against
    the same object that ``uvicorn`` loads.

    For tests, ``build_app()`` returns a *new* app; the decorator-registered
    routes in this module are not re-applied. Tests that exercise routes
    defined in this file should use ``app`` directly via ``TestClient(app)``.
    """
    return FastAPI(
        title="Sweave",
        description="Multi-agent orchestration platform",
        lifespan=lifespan,
    )


# Module-level app singleton. Decorators throughout this file attach routes
# to this object. Production: ``uvicorn sweave.web.server:app``. Tests: use
# the same object via starlette.testclient.TestClient.
app = build_app()
app.mount("/static", StaticFiles(directory="sweave/web/static"), name="static")


# ============================================================================
# WebSocket
# ============================================================================


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    state: AppState = websocket.app.state.app_state
    await websocket.accept()
    if state.event_bus is not None:
        await state.event_bus.subscribe(websocket)
    try:
        while True:
            # Clients don't currently send anything; the receive drains the
            # socket so disconnect detection still works.
            await websocket.receive_text()
    except WebSocketDisconnect:
        if state.event_bus is not None:
            await state.event_bus.unsubscribe(websocket)


# ============================================================================
# Routers: split out in M1.prep step 2 (see sweave/web/routers/).
# ============================================================================

from sweave.web.routers import (
    agents as _agents_router,
    config as _config_router,
    delegations as _delegations_router,
    fs as _fs_router,
    mcp as _mcp_router,
    memory as _memory_router,
    projects as _projects_router,
    specialists as _specialists_router,
    tasks as _tasks_router,
    worktrees as _worktrees_router,
)

for _r in (
    _agents_router.router,
    _config_router.router,
    _delegations_router.router,
    _fs_router.router,
    _mcp_router.router,
    _memory_router.router,
    _projects_router.router,
    _specialists_router.router,
    _tasks_router.router,
    _worktrees_router.router,
):
    app.include_router(_r)


# ============================================================================
# SPA routes
# ============================================================================


INDEX_HTML = Path("sweave/web/static/index.html")


def _read_index_html() -> str:
    if INDEX_HTML.exists():
        return INDEX_HTML.read_text(encoding="utf-8")
    return "<h1>Sweave UI not found</h1><p>index.html missing</p>"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_read_index_html())


@app.get("/agents", response_class=HTMLResponse)
async def agents_page():
    return HTMLResponse(_read_index_html())


@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page():
    return HTMLResponse(_read_index_html())


@app.get("/worktrees", response_class=HTMLResponse)
async def worktrees_page():
    return HTMLResponse(_read_index_html())


@app.get("/memory", response_class=HTMLResponse)
async def memory_page():
    return HTMLResponse(_read_index_html())


@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return HTMLResponse(_read_index_html())


def main():
    import uvicorn

    config = ConfigManager().get()
    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
