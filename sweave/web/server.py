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

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import jinja2
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sweave.config.manager import ConfigManager
from sweave.config.schemas import AgentSpec
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
# Request models
# ============================================================================


class AgentCreate(BaseModel):
    name: str = Field(..., description="Unique agent name")
    role: str = Field(..., description="Role identifier (e.g., backend, frontend)")
    model: str = Field(..., description="Model to use (from models.yaml)")
    system_prompt: str = Field(..., description="System prompt for the agent")
    description: str = Field("", description="Human-readable description")
    tools: list[str] = Field(default_factory=list, description="Additional tools")
    harness: str = Field("opencode", description="Harness to use")


class AgentUpdate(BaseModel):
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[list[str]] = None
    harness: Optional[str] = None


class TaskRequest(BaseModel):
    task: str
    agent: Optional[str] = None
    model: Optional[str] = None


class TaskResponse(BaseModel):
    success: bool
    agent: str
    task_id: str
    output: str
    error: Optional[str] = None


class RoutingRequest(BaseModel):
    task: str


class RoutingResponse(BaseModel):
    agent: str
    model: str
    confidence: float
    reasoning: str
    matched_rule: Optional[str] = None


class ModelUpdateRequest(BaseModel):
    role: str
    model: str


class RuleAddRequest(BaseModel):
    pattern: str
    agent: str
    model: Optional[str] = None


class ProjectCreateRequest(BaseModel):
    name: str
    path: str
    description: str = ""


class SessionCreateRequest(BaseModel):
    name: str
    project_name: Optional[str] = None


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
# Agent routes
# ============================================================================


@app.get("/api/agents")
async def list_agents(state: AppState = Depends(get_state)):
    config = state.config_manager.get()
    builtin: dict = {}
    for role in config.models.roles:
        if role == "orchestrator":
            continue
        builtin[role] = {
            "name": role,
            "role": role,
            "model": state.config_manager.resolve_model(role),
            "description": f"Built-in {role} specialist",
            "builtin": True,
            "dynamic": False,
        }
    dynamic: dict = {}
    for name, spec in state.dynamic_agents.items():
        dynamic[name] = {
            "name": spec.name,
            "role": spec.role,
            "model": spec.model,
            "system_prompt": spec.system_prompt,
            "description": (
                spec.system_prompt[:100] + "..."
                if len(spec.system_prompt) > 100
                else spec.system_prompt
            ),
            "builtin": False,
            "dynamic": True,
        }
    return {"builtin": builtin, "dynamic": dynamic}


@app.post("/api/agents")
async def create_agent(agent: AgentCreate, state: AppState = Depends(get_state)):
    if agent.name in state.dynamic_agents:
        raise HTTPException(400, f"Agent '{agent.name}' already exists")
    config = state.config_manager.get()
    if agent.role in config.models.roles and agent.role != "orchestrator":
        raise HTTPException(400, f"Role '{agent.role}' is a built-in role")
    spec = AgentSpec(
        name=agent.name,
        role=agent.role,
        model=agent.model,
        system_prompt=agent.system_prompt,
        worktree_path=Path(""),
        memory_bank=config.memory.hindsight.bank_id,
        tools=agent.tools,
        harness=agent.harness,
    )
    state.dynamic_agents[agent.name] = spec
    await state.save_dynamic_agents()
    await _broadcast(state, "agent_created", {"name": agent.name, "role": agent.role})
    return {"success": True, "agent": agent.name}


@app.get("/api/agents/{name}")
async def get_agent(name: str, state: AppState = Depends(get_state)):
    if name in state.dynamic_agents:
        spec = state.dynamic_agents[name]
        return {
            "name": spec.name,
            "role": spec.role,
            "model": spec.model,
            "system_prompt": spec.system_prompt,
            "tools": spec.tools,
            "harness": spec.harness,
            "dynamic": True,
        }
    config = state.config_manager.get()
    if name in config.models.roles and name != "orchestrator":
        return {
            "name": name,
            "role": name,
            "model": config.resolve_model(name),
            "description": f"Built-in {name} specialist",
            "builtin": True,
            "dynamic": False,
        }
    raise HTTPException(404, f"Agent '{name}' not found")


@app.put("/api/agents/{name}")
async def update_agent(
    name: str, update: AgentUpdate, state: AppState = Depends(get_state)
):
    if name not in state.dynamic_agents:
        raise HTTPException(404, f"Dynamic agent '{name}' not found")
    config = state.config_manager.get()
    if name in config.models.roles and name != "orchestrator":
        raise HTTPException(400, "Cannot update built-in agent")
    spec = state.dynamic_agents[name]
    if update.model:
        spec.model = update.model
    if update.system_prompt:
        spec.system_prompt = update.system_prompt
    if update.description:
        spec.system_prompt = update.description
    if update.tools:
        spec.tools = update.tools
    if update.harness:
        spec.harness = update.harness
    await state.save_dynamic_agents()
    await _broadcast(state, "agent_updated", {"name": name})
    return {"success": True, "agent": name}


@app.delete("/api/agents/{name}")
async def delete_agent(name: str, state: AppState = Depends(get_state)):
    if name not in state.dynamic_agents:
        raise HTTPException(404, f"Dynamic agent '{name}' not found")
    del state.dynamic_agents[name]
    await state.save_dynamic_agents()
    await _broadcast(state, "agent_deleted", {"name": name})
    return {"success": True}


# ============================================================================
# Tasks
# ============================================================================


@app.post("/api/tasks", response_model=TaskResponse)
async def run_task(request: TaskRequest, state: AppState = Depends(get_state)):
    if not state.router or not state.delegate_tool:
        raise HTTPException(503, "Services not initialized")
    if request.agent:
        decision = state.router._llm_fallback(request.task)
        decision.agent = request.agent
        decision.model = request.model or state.config_manager.resolve_model(request.agent)
    else:
        decision = state.router.route(request.task)
        if request.model:
            decision.model = request.model
    result = await state.delegate_tool.execute(
        agent=decision.agent,
        task=request.task,
        model=request.model or decision.model,
    )
    await _broadcast(
        state,
        "task_completed",
        {
            "task": request.task,
            "agent": result.agent,
            "task_id": result.task_id,
            "success": result.success,
        },
    )
    return TaskResponse(
        success=result.success,
        agent=result.agent,
        task_id=result.task_id,
        output=result.output,
        error=result.error,
    )


@app.post("/api/route", response_model=RoutingResponse)
async def route_task(request: RoutingRequest, state: AppState = Depends(get_state)):
    decision = state.router.route(request.task)
    return RoutingResponse(
        agent=decision.agent,
        model=decision.model or "",
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        matched_rule=decision.matched_rule.pattern if decision.matched_rule else None,
    )


# ============================================================================
# Worktrees
# ============================================================================


@app.get("/api/worktrees")
async def list_worktrees(state: AppState = Depends(get_state)):
    worktrees = state.worktree_manager.list_worktrees()
    return {
        "worktrees": [
            {
                "path": str(wt.path),
                "branch": wt.branch,
                "task_id": wt.task_id,
                "agent": wt.agent_name,
                "created_at": wt.created_at.isoformat(),
                "pr_url": wt.pr_url,
            }
            for wt in worktrees
        ]
    }


@app.post("/api/worktrees/clean")
async def clean_worktrees(state: AppState = Depends(get_state)):
    worktrees = state.worktree_manager.list_worktrees()
    for wt in worktrees:
        state.worktree_manager.remove_worktree(wt.task_id, wt.agent_name, force=True)
    await _broadcast(state, "worktrees_cleaned", {"count": len(worktrees)})
    return {"success": True, "cleaned": len(worktrees)}


@app.delete("/api/worktrees/{task_id}/{agent_name}")
async def remove_worktree_endpoint(
    task_id: str, agent_name: str, state: AppState = Depends(get_state)
):
    success = state.worktree_manager.remove_worktree(task_id, agent_name, force=True)
    if success:
        await _broadcast(
            state, "worktree_removed", {"task_id": task_id, "agent": agent_name}
        )
    return {"success": success}


# ============================================================================
# Memory
# ============================================================================


@app.post("/api/memory/recall")
async def memory_recall(
    query: str, bank_id: Optional[str] = None, limit: int = 10, state: AppState = Depends(get_state)
):
    return await state.memory_tool.execute("recall", query=query, bank_id=bank_id, limit=limit)


@app.post("/api/memory/retain")
async def memory_retain(
    content: str, bank_id: Optional[str] = None, tags: list[str] = None, state: AppState = Depends(get_state)
):
    return await state.memory_tool.execute("retain", content=content, bank_id=bank_id, tags=tags)


@app.post("/api/memory/reflect")
async def memory_reflect(
    query: str, bank_id: Optional[str] = None, state: AppState = Depends(get_state)
):
    return await state.memory_tool.execute("reflect", query=query, bank_id=bank_id)


# ============================================================================
# Config / models / rules / harnesses
# ============================================================================


@app.get("/api/config")
async def get_config(state: AppState = Depends(get_state)):
    return state.config_manager.get().model_dump(exclude_none=True)


@app.get("/api/models")
async def get_models(state: AppState = Depends(get_state)):
    models = state.config_manager.get_models()
    return {
        "roles": {
            role: {
                "default": role_config.default,
                "aliases": role_config.aliases,
                "provider": role_config.provider,
            }
            for role, role_config in models.roles.items()
        }
    }


@app.post("/api/models")
async def set_model(request: ModelUpdateRequest, state: AppState = Depends(get_state)):
    state.config_manager.update_model(request.role, request.model)
    await _broadcast(
        state, "model_changed", {"role": request.role, "model": request.model}
    )
    return {"success": True, "role": request.role, "model": request.model}


@app.get("/api/rules")
async def get_rules(state: AppState = Depends(get_state)):
    routing = state.config_manager.get_routing()
    return {
        "routes": [
            {"pattern": r.pattern, "agent": r.agent, "model": r.model}
            for r in routing.routes
        ],
        "fallback": routing.fallback,
    }


@app.post("/api/rules")
async def add_rule(request: RuleAddRequest, state: AppState = Depends(get_state)):
    state.config_manager.add_routing_rule(request.pattern, request.agent, request.model)
    await _broadcast(
        state,
        "rule_added",
        {"pattern": request.pattern, "agent": request.agent, "model": request.model},
    )
    return {"success": True}


@app.get("/api/harnesses")
async def get_harnesses():
    from sweave.harness import detect_all_harnesses, get_opencode_models

    harnesses = await detect_all_harnesses()
    models = await get_opencode_models()
    return {
        "harnesses": [
            {
                "name": h.name,
                "display_name": h.display_name,
                "command": h.command,
                "version": h.version,
                "providers": h.providers,
                "models": h.models,
            }
            for h in harnesses
        ],
        "opencode_models": models,
    }


@app.post("/api/models/regenerate")
async def api_regenerate_models():
    import subprocess

    try:
        result = subprocess.run(
            ["python", "scripts/generate_models.py"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to regenerate models: {e}")


@app.post("/api/memory/init")
async def api_init_memory():
    return {
        "success": True,
        "message": "Memory backend configuration updated. Run 'python scripts/setup_hindsight.py init' for full setup.",
    }


# ============================================================================
# Projects / sessions (delegated to sweave.api.projects which still holds the
# helpers today; the routers/ split in step 2 will own these.)
# ============================================================================


@app.post("/api/projects")
async def api_create_project(request: ProjectCreateRequest):
    from sweave.api.projects import ProjectCreate, create_project

    try:
        project = await create_project(
            ProjectCreate(
                name=request.name, path=request.path, description=request.description
            )
        )
        return {"success": True, "project": project}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects")
async def api_list_projects():
    from sweave.api.projects import list_projects

    return {"projects": await list_projects()}


@app.get("/api/projects/active")
async def api_get_active_project():
    from sweave.api.projects import get_active_project

    return await get_active_project()


@app.get("/api/projects/{name}")
async def api_get_project(name: str):
    from sweave.api.projects import get_project

    project = await get_project(name)
    if not project:
        raise HTTPException(404, f"Project '{name}' not found")
    return project


@app.post("/api/projects/{name}/active")
async def api_set_active_project(name: str):
    from sweave.api.projects import set_active_project

    try:
        return await set_active_project(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/projects/{name}")
async def api_delete_project(name: str):
    from sweave.api.projects import delete_project

    return await delete_project(name)


@app.post("/api/sessions")
async def api_create_session(request: SessionCreateRequest):
    from sweave.api.projects import SessionCreate, create_session

    try:
        session = await create_session(
            SessionCreate(name=request.name, project_name=request.project_name)
        )
        return {"success": True, "session": session}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/sessions")
async def api_list_sessions(project_name: Optional[str] = None):
    from sweave.api.projects import list_sessions

    return {"sessions": await list_sessions(project_name)}


@app.get("/api/sessions/active")
async def api_get_active_session():
    from sweave.api.projects import get_active_session

    session = await get_active_session()
    if session:
        return session
    return None


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    from sweave.api.projects import get_session

    session = await get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session '{session_id}' not found")
    return session


@app.post("/api/sessions/{session_id}/active")
async def api_set_active_session(session_id: str):
    from sweave.api.projects import set_active_session

    try:
        return await set_active_session(session_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    from sweave.api.projects import delete_session

    return await delete_session(session_id)


@app.post("/api/sessions/{session_id}/messages")
async def api_add_message(session_id: str, message: dict):
    from sweave.api.projects import MessageCreate, add_message

    try:
        msg = await add_message(session_id, MessageCreate(**message))
        return {"success": True, "message": msg}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except TypeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/memory/banks")
async def api_get_memory_banks():
    from sweave.api.projects import get_memory_banks

    return {"banks": await get_memory_banks()}


# ============================================================================
# File-system browser
# ============================================================================


from pathlib import Path as PathLib  # noqa: E402  (kept here for diff clarity)


class DirectoryBrowser:
    """Cross-platform directory browser for the project picker."""

    @staticmethod
    def get_drives() -> list[dict]:
        import platform
        import string

        system = platform.system()
        if system == "Windows":
            drives: list[dict] = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if PathLib(drive).exists():
                    drives.append({"name": f"{letter}:", "path": drive, "is_dir": True})
            return drives
        return [{"name": "/", "path": "/", "is_dir": True}]

    @staticmethod
    def list_directory(path: str) -> dict:
        try:
            p = PathLib(path).expanduser().resolve()
            if not p.exists():
                return {"error": f"Path does not exist: {path}"}
            if not p.is_dir():
                return {"error": f"Not a directory: {path}"}
            parent = str(p.parent) if p.parent != p else None
            entries: list[dict] = []
            if parent and parent != str(p):
                entries.append({"name": "..", "path": parent, "is_dir": True})
            try:
                for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    try:
                        if not entry.is_dir():
                            continue
                        entries.append({"name": entry.name, "path": str(entry), "is_dir": True})
                    except (PermissionError, OSError):
                        continue
            except PermissionError:
                return {"error": f"Permission denied: {path}"}
            return {
                "path": str(p),
                "parent": parent,
                "entries": entries[:200],
                "total": len(entries),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def validate_path(path: str) -> dict:
        try:
            p = PathLib(path).expanduser().resolve()
            if not p.exists():
                return {"valid": False, "error": "Path does not exist"}
            if not p.is_dir():
                return {"valid": False, "error": "Path is not a directory"}
            return {"valid": True, "path": str(p), "name": p.name}
        except Exception as e:
            return {"valid": False, "error": str(e)}


@app.get("/api/fs/drives")
async def api_get_drives():
    return {"drives": DirectoryBrowser.get_drives()}


@app.get("/api/fs/list")
async def api_list_directory(path: str):
    result = DirectoryBrowser.list_directory(path)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/fs/validate")
async def api_validate_path(request: Request):
    body = await request.json()
    return DirectoryBrowser.validate_path(body.get("path", ""))


@app.post("/api/fs/create")
async def api_create_directory(request: Request):
    body = await request.json()
    path = body.get("path", "")
    name = body.get("name", "")
    if not path or not name:
        raise HTTPException(400, "Path and name required")
    try:
        safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_", " ")).strip()
        if not safe_name:
            raise HTTPException(400, "Invalid name")
        p = PathLib(path).expanduser()
        if p.is_file():
            p = p.parent
        elif not p.is_dir():
            p = PathLib(path).parent
        new_path = (p / safe_name).resolve()
        if new_path.exists():
            raise HTTPException(400, f"Path already exists: {new_path}")
        new_path.mkdir(parents=True, exist_ok=False)
        return {"success": True, "path": str(new_path), "name": safe_name}
    except FileExistsError:
        raise HTTPException(400, "Path already exists")
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


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
