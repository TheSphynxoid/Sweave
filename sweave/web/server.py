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

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
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
    config_manager = ConfigManager()
    config_manager.load()
    project_manager.load()

    state = AppState.build(config_manager)
    await state.load_dynamic_agents()
    app.state.app_state = state
    logger.info("AppState built; %d dynamic agents loaded", len(state.dynamic_agents))

    try:
        yield
    finally:
        # Close any open WS connections cleanly
        async with state.active_connections_lock:
            for ws in list(state.active_connections):
                try:
                    await ws.close()
                except Exception:
                    pass
            state.active_connections.clear()


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


async def _broadcast(state: AppState, event: str, data: dict) -> None:
    """Broadcast a JSON message to all connected WebSocket clients."""
    payload = {
        "event": event,
        "data": data,
        "timestamp": datetime.now().isoformat(),
    }
    msg = json.dumps(payload)
    async with state.active_connections_lock:
        dead: list[WebSocket] = []
        for ws in state.active_connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                state.active_connections.remove(ws)
            except ValueError:
                pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    state: AppState = websocket.app.state.app_state
    await websocket.accept()
    async with state.active_connections_lock:
        state.active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        async with state.active_connections_lock:
            try:
                state.active_connections.remove(websocket)
            except ValueError:
                pass


# ============================================================================
# Routers: split out in M1.prep step 2 (see sweave/web/routers/).
# ============================================================================

from sweave.web.routers import (
    agents as _agents_router,
    config as _config_router,
    delegations as _delegations_router,
    fs as _fs_router,
    memory as _memory_router,
    projects as _projects_router,
    tasks as _tasks_router,
    worktrees as _worktrees_router,
)

for _r in (
    _agents_router.router,
    _config_router.router,
    _delegations_router.router,
    _fs_router.router,
    _memory_router.router,
    _projects_router.router,
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
