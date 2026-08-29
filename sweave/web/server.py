from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import asyncio
import uuid
import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict

from sweave.config.manager import ConfigManager
from sweave.config.schemas import AgentSpec, RoutingRule
from sweave.router.router import RuleRouter
from sweave.workspace.manager import WorktreeManager
from sweave.harness.base import harness_registry, Message, AgentResult
from sweave.tools import DelegateTaskTool, RouteTaskTool, WorktreeTool, MemoryTool
from sweave.memory.backends import MemoryFactory
from sweave.projects import project_manager
from sweave.api.projects import (
    create_project, list_projects, get_project, set_active_project, delete_project,
    create_session, list_sessions, get_active_session, set_active_session, get_active_project,
    get_session, add_message, delete_session, get_memory_banks,
    ProjectCreate, SessionCreate, MessageCreate
)


# Global instances
config_manager = ConfigManager()
router: RuleRouter | None = None
worktree_manager: WorktreeManager | None = None
delegate_tool: DelegateTaskTool | None = None
route_tool: RouteTaskTool | None = None
worktree_tool: WorktreeTool | None = None
memory_tool: MemoryTool | None = None

# Load projects on startup
project_manager.load()

# WebSocket connections for real-time updates
active_connections: list[WebSocket] = []

# Agent registry for dynamic agents
dynamic_agents: dict[str, AgentSpec] = {}


app = FastAPI(title="Sweave", description="Multi-agent orchestration platform")

# Static files and templates
app.mount("/static", StaticFiles(directory="sweave/web/static"), name="static")
# Simple template rendering function - avoids Jinja2Templates cache issues on Windows
import jinja2

_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader("sweave/web/templates"),
    autoescape=True,
    enable_async=False,  # Use sync rendering to avoid event loop issues
    cache_size=-1,  # Disable cache
)

def render_template(template_name, context):
    """Render a template with the given context."""
    template = _jinja_env.get_template(template_name)
    return template.render(context)

class Templates:
    """Simple template wrapper matching Jinja2Templates interface."""
    @staticmethod
    def TemplateResponse(template_name, context, status_code=200, headers=None, media_type=None):
        from fastapi.responses import HTMLResponse
        content = render_template(template_name, context)
        return HTMLResponse(content, status_code=status_code, headers=headers, media_type=media_type)

templates = Templates()


class AgentCreate(BaseModel):
    """Request model for creating a specialist agent."""
    name: str = Field(..., description="Unique agent name")
    role: str = Field(..., description="Role identifier (e.g., backend, frontend)")
    model: str = Field(..., description="Model to use (from models.yaml)")
    system_prompt: str = Field(..., description="System prompt for the agent")
    description: str = Field("", description="Human-readable description")
    tools: list[str] = Field(default_factory=list, description="Additional tools")
    harness: str = Field("opencode", description="Harness to use")


class AgentUpdate(BaseModel):
    """Request model for updating an agent."""
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[list[str]] = None
    harness: Optional[str] = None


class TaskRequest(BaseModel):
    """Request model for running a task."""
    task: str
    agent: Optional[str] = None
    model: Optional[str] = None


class TaskResponse(BaseModel):
    """Response model for task execution."""
    success: bool
    agent: str
    task_id: str
    output: str
    error: Optional[str] = None


class RoutingRequest(BaseModel):
    task: str


class ModelUpdateRequest(BaseModel):
    role: str
    model: str


class RuleAddRequest(BaseModel):
    pattern: str
    agent: str
    model: Optional[str] = None


class RoutingResponse(BaseModel):
    agent: str
    model: str
    confidence: float
    reasoning: str
    matched_rule: Optional[str] = None


@app.on_event("startup")
async def startup():
    """Initialize services on startup."""
    global router, worktree_manager, delegate_tool, route_tool, worktree_tool, memory_tool
    
    config_manager.load()
    config = config_manager.get()
    
    # Load projects
    project_manager.load()
    
    router = RuleRouter(config_manager)
    worktree_manager = WorktreeManager(config.git.worktree_base)
    delegate_tool = DelegateTaskTool(config_manager, worktree_manager)
    route_tool = RouteTaskTool(config_manager)
    worktree_tool = WorktreeTool(worktree_manager)
    memory_tool = MemoryTool(config_manager)
    
    # Load dynamic agents from config
    await load_dynamic_agents()


async def load_dynamic_agents():
    """Load dynamic agents from config file."""
    global dynamic_agents
    agents_file = Path("agents.yaml")
    if agents_file.exists():
        import yaml
        with open(agents_file) as f:
            data = yaml.safe_load(f) or {}
        for agent_data in data.get("agents", []):
            spec = AgentSpec(**agent_data)
            dynamic_agents[spec.name] = spec


async def save_dynamic_agents():
    """Save dynamic agents to config file."""
    agents_file = Path("agents.yaml")
    agents_data = []
    for spec in dynamic_agents.values():
        d = asdict(spec)
        # Convert Path to string for YAML serialization
        d["worktree_path"] = str(d["worktree_path"])
        agents_data.append(d)
    data = {"agents": agents_data}
    import yaml
    with open(agents_file, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


async def broadcast_update(event: str, data: dict):
    """Broadcast update to all connected WebSocket clients."""
    message = json.dumps({"event": event, "data": data, "timestamp": datetime.now().isoformat()})
    for ws in active_connections:
        try:
            await ws.send_text(message)
        except Exception:
            pass


# WebSocket endpoint for real-time updates
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.remove(websocket)


# ==================== Agent Management API ====================

@app.get("/api/agents")
async def list_agents():
    """List all specialist agents (built-in + dynamic)."""
    config = config_manager.get()
    builtin = {}
    
    # Built-in agents from models config
    for role in config.models.roles:
        if role != "orchestrator":
            builtin[role] = {
                "name": role,
                "role": role,
                "model": config_manager.resolve_model(role),
                "description": f"Built-in {role} specialist",
                "builtin": True,
                "dynamic": False,
            }
    
    # Dynamic agents
    dynamic = {}
    for name, spec in dynamic_agents.items():
        dynamic[name] = {
            "name": spec.name,
            "role": spec.role,
            "model": spec.model,
            "system_prompt": spec.system_prompt,
            "description": spec.system_prompt[:100] + "..." if len(spec.system_prompt) > 100 else spec.system_prompt,
            "builtin": False,
            "dynamic": True,
        }
    
    return {"builtin": builtin, "dynamic": dynamic}


@app.post("/api/agents")
async def create_agent(agent: AgentCreate):
    """Create a new specialist agent."""
    if agent.name in dynamic_agents:
        raise HTTPException(400, f"Agent '{agent.name}' already exists")
    
    # Check if role conflicts with built-in
    config = config_manager.get()
    if agent.role in config.models.roles and agent.role != "orchestrator":
        raise HTTPException(400, f"Role '{agent.role}' is a built-in role")
    
    spec = AgentSpec(
        name=agent.name,
        role=agent.role,
        model=agent.model,
        system_prompt=agent.system_prompt,
        worktree_path=Path(""),  # Will be set per task
        memory_bank=config.memory.hindsight.bank_id,
        tools=agent.tools,
        harness=agent.harness,
    )
    
    dynamic_agents[agent.name] = spec
    await save_dynamic_agents()
    await broadcast_update("agent_created", {"name": agent.name, "role": agent.role})
    
    return {"success": True, "agent": agent.name}


@app.get("/api/agents/{name}")
async def get_agent(name: str):
    """Get agent details."""
    # Check dynamic first
    if name in dynamic_agents:
        spec = dynamic_agents[name]
        return {
            "name": spec.name,
            "role": spec.role,
            "model": spec.model,
            "system_prompt": spec.system_prompt,
            "tools": spec.tools,
            "harness": spec.harness,
            "dynamic": True,
        }
    
    # Check built-in
    config = config_manager.get()
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
async def update_agent(name: str, update: AgentUpdate):
    """Update a dynamic agent."""
    if name not in dynamic_agents:
        raise HTTPException(404, f"Dynamic agent '{name}' not found")
    
    # Can't update built-in agents
    config = config_manager.get()
    if name in config.models.roles and name != "orchestrator":
        raise HTTPException(400, "Cannot update built-in agent")
    
    spec = dynamic_agents[name]
    if update.model:
        spec.model = update.model
    if update.system_prompt:
        spec.system_prompt = update.system_prompt
    if update.description:
        spec.system_prompt = update.description  # Using description as prompt prefix
    if update.tools:
        spec.tools = update.tools
    if update.harness:
        spec.harness = update.harness
    
    await save_dynamic_agents()
    await broadcast_update("agent_updated", {"name": name})
    
    return {"success": True, "agent": name}


@app.delete("/api/agents/{name}")
async def delete_agent(name: str):
    """Delete a dynamic agent."""
    if name not in dynamic_agents:
        raise HTTPException(404, f"Dynamic agent '{name}' not found")
    
    del dynamic_agents[name]
    await save_dynamic_agents()
    await broadcast_update("agent_deleted", {"name": name})
    
    return {"success": True}


# ==================== Task Execution API ====================

@app.post("/api/tasks", response_model=TaskResponse)
async def run_task(request: TaskRequest):
    """Execute a task through the orchestrator."""
    if not router or not delegate_tool:
        raise HTTPException(503, "Services not initialized")
    
    # Route task
    if request.agent:
        decision = router._llm_fallback(request.task)
        decision.agent = request.agent
        decision.model = request.model or config_manager.resolve_model(request.agent)
    else:
        decision = router.route(request.task)
        if request.model:
            decision.model = request.model
    
    # Execute
    result = await delegate_tool.execute(
        agent=decision.agent,
        task=request.task,
        model=request.model or decision.model,
    )
    
    await broadcast_update("task_completed", {
        "task": request.task,
        "agent": result.agent,
        "task_id": result.task_id,
        "success": result.success,
    })
    
    return TaskResponse(
        success=result.success,
        agent=result.agent,
        task_id=result.task_id,
        output=result.output,
        error=result.error,
    )


@app.post("/api/route", response_model=RoutingResponse)
async def route_task(request: RoutingRequest):
    """Get routing decision for a task without executing."""
    if not router:
        raise HTTPException(503, "Router not initialized")
    
    decision = router.route(request.task)
    return RoutingResponse(
        agent=decision.agent,
        model=decision.model or "",
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        matched_rule=decision.matched_rule.pattern if decision.matched_rule else None,
    )


# ==================== Worktree API ====================

@app.get("/api/worktrees")
async def list_worktrees():
    """List all active worktrees."""
    if not worktree_manager:
        raise HTTPException(503, "Worktree manager not initialized")
    
    worktrees = worktree_manager.list_worktrees()
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
async def clean_worktrees():
    """Clean merged worktrees."""
    if not worktree_manager:
        raise HTTPException(503, "Worktree manager not initialized")
    
    worktrees = worktree_manager.list_worktrees()
    for wt in worktrees:
        worktree_manager.remove_worktree(wt.task_id, wt.agent_name, force=True)
    
    await broadcast_update("worktrees_cleaned", {"count": len(worktrees)})
    return {"success": True, "cleaned": len(worktrees)}


# ==================== Memory API ====================

@app.post("/api/memory/recall")
async def memory_recall(query: str, bank_id: Optional[str] = None, limit: int = 10):
    """Recall memories."""
    if not memory_tool:
        raise HTTPException(503, "Memory tool not initialized")
    
    result = await memory_tool.execute("recall", query=query, bank_id=bank_id, limit=limit)
    return result


@app.post("/api/memory/retain")
async def memory_retain(content: str, bank_id: Optional[str] = None, tags: list[str] = None):
    """Retain a memory."""
    if not memory_tool:
        raise HTTPException(503, "Memory tool not initialized")
    
    result = await memory_tool.execute("retain", content=content, bank_id=bank_id, tags=tags)
    return result


@app.post("/api/memory/reflect")
async def memory_reflect(query: str, bank_id: Optional[str] = None):
    """Reflect on memories."""
    if not memory_tool:
        raise HTTPException(503, "Memory tool not initialized")
    
    result = await memory_tool.execute("reflect", query=query, bank_id=bank_id)
    return result


# ==================== Config API ====================

@app.get("/api/config")
async def get_config():
    """Get current configuration."""
    config = config_manager.get()
    return config.model_dump(exclude_none=True)


@app.get("/api/models")
async def get_models():
    """Get model configuration."""
    models = config_manager.get_models()
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
async def set_model(request: ModelUpdateRequest):
    """Set model for a role."""
    config_manager.update_model(request.role, request.model)
    await broadcast_update("model_changed", {"role": request.role, "model": request.model})
    return {"success": True, "role": request.role, "model": request.model}


@app.get("/api/rules")
async def get_rules():
    """Get routing rules."""
    routing = config_manager.get_routing()
    return {
        "routes": [
            {"pattern": r.pattern, "agent": r.agent, "model": r.model}
            for r in routing.routes
        ],
        "fallback": routing.fallback,
    }


@app.post("/api/rules")
async def add_rule(request: RuleAddRequest):
    """Add a routing rule."""
    config_manager.add_routing_rule(request.pattern, request.agent, request.model)
    await broadcast_update("rule_added", {"pattern": request.pattern, "agent": request.agent, "model": request.model})
    return {"success": True}


@app.get("/api/harnesses")
async def get_harnesses():
    """Get available harnesses."""
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


# ==================== Project/Session API ====================

class ProjectCreateRequest(BaseModel):
    name: str
    path: str
    description: str = ""


class SessionCreateRequest(BaseModel):
    name: str
    project_name: Optional[str] = None


@app.post("/api/projects")
async def api_create_project(request: ProjectCreateRequest):
    """Create a new project from a folder path."""
    try:
        project = await create_project(ProjectCreate(
            name=request.name,
            path=request.path,
            description=request.description,
        ))
        return {"success": True, "project": project}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects")
async def api_list_projects():
    return {"projects": await list_projects()}


@app.get("/api/projects/active")
async def api_get_active_project():
    return await get_active_project()


@app.get("/api/projects/{name}")
async def api_get_project(name: str):
    project = await get_project(name)
    if not project:
        raise HTTPException(404, f"Project '{name}' not found")
    return project


@app.post("/api/projects/{name}/active")
async def api_set_active_project(name: str):
    try:
        return await set_active_project(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/projects/{name}")
async def api_delete_project(name: str):
    return await delete_project(name)


# ==================== Sessions ====================

@app.post("/api/sessions")
async def api_create_session(request: SessionCreateRequest):
    try:
        session = await create_session(SessionCreate(
            name=request.name,
            project_name=request.project_name,
        ))
        return {"success": True, "session": session}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/sessions")
async def api_list_sessions(project_name: Optional[str] = None):
    return {"sessions": await list_sessions(project_name)}


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    session = await get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session '{session_id}' not found")
    return session


@app.post("/api/sessions/{session_id}/active")
async def api_set_active_session(session_id: str):
    try:
        return await set_active_session(session_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/sessions/active")
async def api_get_active_session():
    session = await get_active_session()
    if session:
        return session
    return None


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    return await delete_session(session_id)


@app.post("/api/sessions/{session_id}/messages")
async def api_add_message(session_id: str, message: MessageCreate):
    try:
        msg = await add_message(session_id, message)
        return {"success": True, "message": msg}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/memory/banks")
async def api_get_memory_banks():
    """Get available memory banks (global, project, session)."""
    return {"banks": await get_memory_banks()}


# ==================== Models / Memory Init / Worktree Delete ====================

@app.delete("/api/worktrees/{task_id}/{agent_name}")
async def remove_worktree_endpoint(task_id: str, agent_name: str):
    """Remove a specific worktree."""
    if not worktree_manager:
        raise HTTPException(503, "Worktree manager not initialized")
    success = worktree_manager.remove_worktree(task_id, agent_name, force=True)
    if success:
        await broadcast_update("worktree_removed", {"task_id": task_id, "agent": agent_name})
    return {"success": success}


@app.post("/api/models/regenerate")
async def api_regenerate_models():
    """Regenerate models.yaml from models.dev."""
    try:
        import subprocess
        result = subprocess.run(
            ["python", "scripts/generate_models.py"],
            capture_output=True, text=True, timeout=30,
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
    """Initialize memory backend (placeholder - full init requires manual script)."""
    return {
        "success": True,
        "message": "Memory backend configuration updated. Run 'python scripts/setup_hindsight.py init' for full setup.",
    }


# ==================== Directory Browser (for Project Picker) ====================

from pathlib import Path as PathLib


class DirectoryBrowser:
    """Cross-platform directory browser for the project picker."""

    @staticmethod
    def get_drives() -> list[dict]:
        """Get list of available drives/roots."""
        import platform
        import string
        system = platform.system()

        if system == "Windows":
            # Windows: list drive letters C:, D:, etc.
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if PathLib(drive).exists():
                    drives.append({
                        "name": f"{letter}:",
                        "path": drive,
                        "is_dir": True,
                    })
            return drives
        else:
            # Unix: just root
            return [{"name": "/", "path": "/", "is_dir": True}]

    @staticmethod
    def list_directory(path: str) -> dict:
        """List contents of a directory."""
        import os
        try:
            p = PathLib(path).expanduser().resolve()
            if not p.exists():
                return {"error": f"Path does not exist: {path}"}
            if not p.is_dir():
                return {"error": f"Not a directory: {path}"}

            parent = str(p.parent) if p.parent != p else None
            entries = []

            # Add parent entry
            if parent and parent != str(p):
                entries.append({
                    "name": "..",
                    "path": parent,
                    "is_dir": True,
                })

            try:
                for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    try:
                        is_dir = entry.is_dir()
                        if not is_dir:
                            continue  # Skip files
                        entries.append({
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": True,
                        })
                    except (PermissionError, OSError):
                        continue
            except PermissionError:
                return {"error": f"Permission denied: {path}"}

            return {
                "path": str(p),
                "parent": parent,
                "entries": entries[:200],  # Limit to 200 entries
                "total": len(entries),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def validate_path(path: str) -> dict:
        """Check if a path is valid and can be used as a project."""
        try:
            p = PathLib(path).expanduser().resolve()
            if not p.exists():
                return {"valid": False, "error": "Path does not exist"}
            if not p.is_dir():
                return {"valid": False, "error": "Path is not a directory"}
            return {
                "valid": True,
                "path": str(p),
                "name": p.name,
            }
        except Exception as e:
            return {"valid": False, "error": str(e)}


@app.get("/api/fs/drives")
async def api_get_drives():
    """Get available drives/roots for the file browser."""
    return {"drives": DirectoryBrowser.get_drives()}


@app.get("/api/fs/list")
async def api_list_directory(path: str):
    """List contents of a directory path."""
    result = DirectoryBrowser.list_directory(path)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/fs/validate")
async def api_validate_path(request: Request):
    """Validate a project path."""
    body = await request.json()
    path = body.get("path", "")
    return DirectoryBrowser.validate_path(path)


@app.post("/api/fs/create")
async def api_create_directory(request: Request):
    """Create a new directory for a project."""
    body = await request.json()
    path = body.get("path", "")
    name = body.get("name", "")

    if not path or not name:
        raise HTTPException(400, "Path and name required")

    try:
        # Sanitize name (no slashes, no special chars)
        safe_name = "".join(c for c in name if c.isalnum() or c in ('-', '_', ' ')).strip()
        if not safe_name:
            raise HTTPException(400, "Invalid name")

        # If path is a file (selected file), use parent
        p = PathLib(path).expanduser()
        if p.is_file():
            p = p.parent
        elif not p.is_dir():
            # Maybe it's the parent of the new project
            p = PathLib(path).parent

        new_path = (p / safe_name).resolve()

        if new_path.exists():
            raise HTTPException(400, f"Path already exists: {new_path}")

        new_path.mkdir(parents=True, exist_ok=False)

        return {
            "success": True,
            "path": str(new_path),
            "name": safe_name,
        }
    except FileExistsError:
        raise HTTPException(400, f"Path already exists")
    except PermissionError:
        raise HTTPException(403, f"Permission denied")
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== Web UI ====================

# Serve the new single-page app (Odysseus-inspired) from static
INDEX_HTML = Path("sweave/web/static/index.html")


def _read_index_html() -> str:
    """Read the index.html file fresh each time (avoids caching issues)."""
    if INDEX_HTML.exists():
        with open(INDEX_HTML, encoding="utf-8") as f:
            return f.read()
    return "<h1>Sweave UI not found</h1><p>index.html missing</p>"


@app.get("/", response_class=HTMLResponse)
async def index():
    """Main app entry — serves the single-page application."""
    return HTMLResponse(_read_index_html())


@app.get("/agents", response_class=HTMLResponse)
async def agents_page():
    """Same SPA — client-side routing."""
    return HTMLResponse(_read_index_html())


@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page():
    """Same SPA — client-side routing."""
    return HTMLResponse(_read_index_html())


@app.get("/worktrees", response_class=HTMLResponse)
async def worktrees_page():
    """Same SPA — client-side routing."""
    return HTMLResponse(_read_index_html())


@app.get("/memory", response_class=HTMLResponse)
async def memory_page():
    """Same SPA — client-side routing."""
    return HTMLResponse(_read_index_html())


@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    """Same SPA — client-side routing."""
    return HTMLResponse(_read_index_html())


def main():
    """Run the web server."""
    import uvicorn
    config = config_manager.get()
    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()