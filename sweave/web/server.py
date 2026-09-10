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
import os
from contextlib import asynccontextmanager
from pathlib import Path

import jinja2
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from sweave.config.manager import ConfigManager
from sweave.projects import project_manager
from sweave.web.deps import get_state as _get_state  # re-exported below as get_state
from sweave.web.state import AppState

logger = logging.getLogger(__name__)

# Re-export the dependency for routers that import ``sweave.web.server.get_state``.
get_state = _get_state


async def _recover_interrupted_delegations(
    delegation_stores, project_manager
) -> int:
    """Boot recovery for delegations orphaned by a previous run.

    A server crash/restart leaves every non-terminal delegation
    (``running``/``queued``) stale on disk: the coroutine driving it
    died with the process. Called from the lifespan BEFORE any turn
    can be accepted, so anything non-terminal in the persisted stores
    is by definition from a previous process. Chat delegations keep
    the partial ``output`` the streaming coalescer persisted, so the
    already-streamed text survives the restart (surfaced on the
    failed record; the UI shows a clear "interrupted" turn instead of
    a phantom active one).

    Returns the number of records recovered (for the boot log).
    """
    project_dirs: list[Path] = [
        Path(p.path) for p in project_manager.list_projects()
    ]
    # The fallback store (chat sessions created without a project) is
    # anchored at ~/.sweave, matching the loop's project_dir_resolver
    # fallback.
    project_dirs.append(Path.home() / ".sweave")
    recovered = 0
    for dir_path in project_dirs:
        try:
            store = await delegation_stores.for_project(dir_path)
            recovered += await store.recover_interrupted()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Boot recovery: could not recover %s: %s", dir_path, e
            )
    return recovered


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
    # Chat-turn crash recovery (client-refresh / server-restart
    # contract): before any turn can be accepted, mark every
    # non-terminal delegation record orphaned by the previous
    # process run as cleanly failed (chat delegations keep their
    # partial streamed `output`). No phantom "running" turns for a
    # freshly loaded UI.
    _recovered = await _recover_interrupted_delegations(
        state.delegation_stores, project_manager
    )
    if _recovered:
        logger.info(
            "Boot recovery: marked %d orphaned non-terminal delegation(s) "
            "as failed (interrupted by server restart)",
            _recovered,
        )
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
    from sweave.runtime.serve_runner import (
        IDLE_SWEEP_INTERVAL_SECONDS,
        ServeRunnerRegistry,
        reclaim_tracked_serves,
    )
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    serve_tracking = Path.home() / ".sweave" / "serves.json"
    serve_registry = ServeRunnerRegistry(
        event_bus=state.event_bus, tracking_path=serve_tracking
    )
    # Reclaim serves orphaned by a previous server run (crash /
    # force-stop) before this registry spawns anything. Entries owned
    # by a still-live server are left alone.
    reclaimed = reclaim_tracked_serves(serve_tracking)
    if reclaimed:
        logger.info(
            "Reclaimed %d orphaned opencode serve(s) from a previous run: %s",
            len(reclaimed), [e.get("pid") for e in reclaimed],
        )
    specialist_runtime = SpecialistRuntime(
        runners=serve_registry,
        event_bus=state.event_bus,
    )
    state.job_runner.specialist_runtime = specialist_runtime

    # M1.7 step 2: build the ChatLoop. The chat endpoint calls
    # ``run_turn`` for every user message; the loop drives the
    # orchestrator specialist through the same SpecialistRuntime the
    # JobRunner uses, with Session-bound session-id callbacks (the
    # step-1 fix). Step 4 wires the transcript system hooks so the
    # runtime owns the per-turn composed prompt. Built here so it
    # can be wired into the routers below.
    from sweave.chat.loop import ChatLoop
    from sweave.chat.transcript import GitSnapshotter

    state.chat_loop = ChatLoop(
        project_manager=project_manager,
        specialist_runtime=specialist_runtime,
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
        project_dir_resolver=lambda name: (
            project_manager.get_project(name).path
            if project_manager.get_project(name) is not None
            else None
        ),
        delegation_stores=state.delegation_stores,
        event_bus=state.event_bus,
        turn_timeout=state.job_runner.turn_timeout,
        model_resolver=lambda agent: config_manager.resolve_model(agent),
        # M1.7 step 4: transcript system hooks
        memory_recall=state.memory_tool.memory,
        memory_bank_id_resolver=lambda name: (
            f"project-{name}" if name else "global"
        ),
        memory_whats_new_recall=state.memory_tool.memory,
        git_snapshotter=GitSnapshotter(),
    )

    # M1.6 step 2: build the DelegationManager from the loaded config.
    # The v2 task endpoint runs ``validate`` before delegating to the
    # runner when ``parent_task_id`` is set; the MCP ``defer`` tool
    # also goes through this gate (via /api/v2/tasks).
    from sweave.runtime.delegation_manager import DelegationManager

    state.delegation_manager = DelegationManager.from_config(config_manager.get())

    # M1.9 step 3 (+ M1.11 no-timeout questions): build the
    # EscalationStore for ask_human / escalate. The store
    # bridges its callable emitter to the WSEventBus so escalation events
    # fan out to every connected WS client (the Children audit +
    # inline Question card patch in place). Persistence is per-delegation
    # JSON files under ``~/.sweave/escalations/`` (the same dir the
    # store module computes from its base_dir arg). timeout None =
    # questions wait indefinitely until answered or skipped.
    from sweave.runtime.escalation import EscalationStore
    from sweave.web.routers.delegations import _all_stores

    async def _flag_delegation_needs_attention(
        delegation_id: str, value: bool
    ) -> None:
        """Flip ``needs_attention`` on the asking delegation.

        Injected into the EscalationStore so EVERY creator (ask_human
        router, permission bridge, stall branch) fulfils the flag
        contract at the store boundary. Same best-effort loop the
        ask_human endpoint uses.
        """
        for store in _all_stores(state):
            if store.get(delegation_id) is not None:
                try:
                    await store.update(delegation_id, needs_attention=value)
                except Exception:  # noqa: BLE001
                    pass
                break

    state.escalation_store = EscalationStore(
        base_dir=Path.home() / ".sweave",
        timeout_seconds=None,
        event_bus=state.event_bus,
        delegation_flagger=_flag_delegation_needs_attention,
    )
    # M1.11: the chat turn holds open on blocking questions, so the
    # loop needs the store (constructed above, after the loop).
    if state.chat_loop is not None:
        state.chat_loop.escalation_store = state.escalation_store

    app.state.app_state = state
    logger.info(
        "AppState built; %d dynamic agents loaded; JobRunner ready "
        "(SpecialistRuntime wired)",
        len(state.dynamic_agents),
    )

    # Export the MCP token into the server's environment so the
    # per-project opencode.json ``{env:SWEAVE_MCP_TOKEN}`` expansion
    # always resolves: opencode serve inherits this process's env,
    # and the opencode-spawned MCP server inherits the serve's. (An
    # unset var expands to "" and the MCP server falls back to the
    # home file -- which works, but a stale inherited value would
    # 401 every tool call; the file is the source of truth, so the
    # export overwrites.) Restored on shutdown so tests embedding
    # the lifespan don't leak it.
    from sweave.mcp import get_or_create_token

    _prev_mcp_token = os.environ.get("SWEAVE_MCP_TOKEN")
    os.environ["SWEAVE_MCP_TOKEN"] = get_or_create_token()

    # Periodic idle-TTL enforcement for opencode serves (the 30-min TTL
    # otherwise never fires -- nothing called sweep_idle before).
    import asyncio

    async def _idle_serve_sweeper() -> None:
        while True:
            await asyncio.sleep(IDLE_SWEEP_INTERVAL_SECONDS)
            try:
                evicted = await serve_registry.sweep_idle()
            except asyncio.CancelledError:
                raise
            except Exception as sweep_err:  # noqa: BLE001
                logger.warning("idle serve sweep failed: %s", sweep_err)
                continue
            if evicted:
                logger.info(
                    "Idle-swept %d opencode serve(s): %s",
                    len(evicted), [r.specialist_name for r in evicted],
                )

    _sweeper_task = asyncio.create_task(_idle_serve_sweeper())

    try:
        yield
    finally:
        _sweeper_task.cancel()
        try:
            await _sweeper_task
        except asyncio.CancelledError:
            pass
        except Exception as sweep_err:  # noqa: BLE001
            logger.warning("idle sweeper shutdown failed: %s", sweep_err)
        # Tear down every serve we spawned so a stop/restart doesn't
        # orphan them (the tracking file ends empty on a clean exit).
        try:
            await serve_registry.shutdown_all()
        except Exception as shutdown_err:  # noqa: BLE001
            logger.warning("serve shutdown_all failed: %s", shutdown_err)
        if _prev_mcp_token is None:
            os.environ.pop("SWEAVE_MCP_TOKEN", None)
        else:
            os.environ["SWEAVE_MCP_TOKEN"] = _prev_mcp_token
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


# R4.2 (2026-09-08): SPA cache headers. Without them the browser
# heuristically caches index.html, so a UI fix keeps "not appearing"
# after a normal reload (the shell references an old hashed bundle).
# index/SPA HTML must revalidate on every load; the hashed /assets
# bundles are content-addressed by vite and safe to cache forever.
@app.middleware("http")
async def _spa_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers.setdefault(
            "Cache-Control", "public, max-age=31536000, immutable"
        )
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response

# M1.9 / R4 step 4: the new wave-1 UI ships from sweave-web/dist.
# When the dist/ artefact exists, mount it as the SPA; the
# /static mount (the old vanilla assets) becomes the fallback.
# The conditional keeps the repo buildable + testable without
# `npm run build` (the v1 vanilla UI is still served when dist/
# is missing).
_SWEAVE_WEB_DIST = Path("sweave-web/dist")
_HAS_WEB_DIST = (_SWEAVE_WEB_DIST / "index.html").exists()
if _HAS_WEB_DIST:
    # Serve the bundled JS + CSS under /assets/ (vite's default
    # asset directory; the dist/index.html references these).
    app.mount(
        "/assets",
        StaticFiles(directory=str(_SWEAVE_WEB_DIST / "assets")),
        name="sweave-web-assets",
    )
else:
    # Fallback: the v1 vanilla UI. Will be removed in step 4.3
    # once the new Playwright suite is in place + the v1 tests
    # are retired.
    app.mount(
        "/static", StaticFiles(directory="sweave/web/static"), name="static"
    )


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

# M1.9 / R4 step 4: the new wave-1 UI (sweave-web/dist) is the
# source of truth. The v1 vanilla UI is the fallback when the
# dist/ artefact is missing (the repo is buildable + testable
# without `npm run build`). The cutover is conditional on the
# dist/ artefact -- a single env var can pin the v1 UI for
# debugging.
_USE_WEB_DIST = _HAS_WEB_DIST and os.environ.get(
    "SWEAVE_UI_VANILLA"
) != "1"
_WEB_DIST_INDEX = _SWEAVE_WEB_DIST / "index.html"
_VANILLA_INDEX = Path("sweave/web/static/index.html")


def _read_index_html() -> str:
    if _USE_WEB_DIST and _WEB_DIST_INDEX.exists():
        return _WEB_DIST_INDEX.read_text(encoding="utf-8")
    if _VANILLA_INDEX.exists():
        return _VANILLA_INDEX.read_text(encoding="utf-8")
    return "<h1>Sweave UI not found</h1><p>index.html missing</p>"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_read_index_html())


# M1.9 / R4 step 4: SPA catch-all for client-side routes. The
# new wave-1 UI uses BrowserRouter (a SPA); the server must
# serve index.html for any non-API path so React Router can
# pick up the URL. The catch-all is only registered when the
# dist/ artefact is present (v1 vanilla UI had dedicated
# routes for /agents, /tasks, etc.; keeping those below for
# the fallback).
if _USE_WEB_DIST:
    # Serve the favicon from the sweave-web public/ directory.
    _FAVICON_PATH = Path("sweave-web/public/favicon.svg")
    if _FAVICON_PATH.exists():
        @app.get("/favicon.svg")
        async def favicon():
            from fastapi.responses import FileResponse
            return FileResponse(str(_FAVICON_PATH), media_type="image/svg+xml")

    @app.get("/{path:path}", response_class=HTMLResponse)
    async def spa_fallback(path: str):
        # Only catch paths that don't look like an API or
        # static asset route. The router's API endpoints are
        # all under /api/*; the WS is /ws; the assets mount
        # is /assets/*.
        if (
            path.startswith("api/")
            or path.startswith("ws")
            or path.startswith("assets/")
            or path.startswith("static/")
            or path == "favicon.svg"
            or "." in path.split("/")[-1]  # any path with a file extension
        ):
            # Let FastAPI's normal routing handle these (or 404
            # for unknown file paths).
            from fastapi import HTTPException

            raise HTTPException(404, "Not Found")
        return HTMLResponse(_read_index_html())


# Legacy vanilla-UI paths (used only when dist/ is missing;
# kept for the M1.x compatibility window).
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
