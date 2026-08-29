"""Agent CRUD routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sweave.config.schemas import AgentSpec
from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


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


@router.get("/api/agents")
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


@router.post("/api/agents")
async def create_agent(agent: AgentCreate, state: AppState = Depends(get_state)):
    if agent.name in state.dynamic_agents:
        raise HTTPException(400, f"Agent '{agent.name}' already exists")
    config = state.config_manager.get()
    if agent.role in config.models.roles and agent.role != "orchestrator":
        raise HTTPException(400, f"Role '{agent.role}' is a built-in role")
    from pathlib import Path

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
    await state.publish("agent_created", {"name": agent.name, "role": agent.role})
    return {"success": True, "agent": agent.name}


@router.get("/api/agents/{name}")
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


@router.put("/api/agents/{name}")
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
    await state.publish("agent_updated", {"name": name})
    return {"success": True, "agent": name}


@router.delete("/api/agents/{name}")
async def delete_agent(name: str, state: AppState = Depends(get_state)):
    if name not in state.dynamic_agents:
        raise HTTPException(404, f"Dynamic agent '{name}' not found")
    del state.dynamic_agents[name]
    await state.save_dynamic_agents()
    await state.publish("agent_deleted", {"name": name})
    return {"success": True}
