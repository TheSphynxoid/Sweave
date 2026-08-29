from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from sweave.config.manager import ConfigManager
from sweave.router.router import RuleRouter
from sweave.workspace.manager import WorktreeManager
from sweave.harness.base import Harness, AgentSpec, harness_registry
from sweave.memory.backends import MemoryFactory
from sweave.projects import project_manager


@dataclass
class DelegationResult:
    """Result of delegating a task to a specialist agent."""
    success: bool
    agent: str
    task_id: str
    output: str
    error: str | None = None


class RouteTaskTool:
    """Tool for routing a task to the appropriate specialist agent."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self.router = RuleRouter(config_manager)
    
    async def execute(self, task: str) -> dict[str, Any]:
        """Route a task and return the decision."""
        decision = self.router.route(task)
        return {
            "agent": decision.agent,
            "model": decision.model,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
            "matched_rule": decision.matched_rule.pattern if decision.matched_rule else None,
        }


class DelegateTaskTool:
    """Tool for delegating a task to a specialist agent."""
    
    def __init__(
        self,
        config_manager: ConfigManager,
        worktree_manager: WorktreeManager,
    ):
        self.config = config_manager
        self.worktree_manager = worktree_manager
        self._active_agents: dict[str, Any] = {}  # session_id -> agent process
    
    async def execute(
        self,
        agent: str,
        task: str,
        model: str | None = None,
        task_id: str | None = None,
    ) -> DelegationResult:
        """Delegate a task to a specialist agent."""
        task_id = task_id or str(uuid.uuid4())[:8]
        
        # Resolve model
        resolved_model = model or self.config.resolve_model(agent)
        
        # Create worktree
        worktree_info = await self.worktree_manager.async_create_worktree(task_id, agent)
        
        # Get agent spec
        spec = self._get_agent_spec(agent, resolved_model, worktree_info)
        
        # Get harness
        harness = harness_registry.get(self.config.get().harness.default)
        if not harness:
            return DelegationResult(
                success=False,
                agent=agent,
                task_id=task_id,
                output="",
                error=f"Harness not found: {self.config.get().harness.default}",
            )
        
        # Spawn agent
        try:
            agent_process = await harness.spawn(spec)
            self._active_agents[task_id] = agent_process
            
            # Send task to agent
            from sweave.harness.base import Message
            result = await agent_process.send(Message(
                type="user",
                content=task,
            ))
            
            return DelegationResult(
                success=result.success,
                agent=agent,
                task_id=task_id,
                output=result.output,
                error=result.error,
            )
        except Exception as e:
            return DelegationResult(
                success=False,
                agent=agent,
                task_id=task_id,
                output="",
                error=str(e),
            )
    
    def _get_agent_spec(self, agent: str, model: str, worktree_info) -> AgentSpec:
        """Get agent specification for a specialist."""
        from sweave.config.schemas import AgentSpec
        
        prompts = {
            "backend": """You are a backend engineering specialist. You excel at:
- API design and implementation (REST, GraphQL, gRPC)
- Database design, migrations, and optimization
- Authentication, authorization, and security
- Server-side architecture and patterns
- Testing and deployment

Work in your assigned worktree. Write clean, well-tested code. Open PRs when complete.""",
            "frontend": """You are a frontend engineering specialist. You excel at:
- React, Vue, Svelte component development
- State management, routing, and data fetching
- CSS, styling, and responsive design
- TypeScript and modern frontend tooling
- Accessibility and performance

Work in your assigned worktree. Write clean, accessible code. Open PRs when complete.""",
            "reviewer": """You are a code review specialist. You excel at:
- Code quality and best practices
- Security vulnerability detection
- Performance optimization
- Architecture and design review
- Testing strategy review

Review code thoroughly. Provide actionable feedback. Approve or request changes.""",
            "orchestrator": """You are the orchestrator. You coordinate specialist agents.
- Route tasks to appropriate specialists
- Synthesize results from multiple agents
- Manage worktrees and delegations
- Track progress and dependencies""",
        }
        
        return AgentSpec(
            name=f"{agent}-specialist",
            role=agent,
            model=model,
            system_prompt=prompts.get(agent, prompts["backend"]),
            worktree_path=worktree_info.path,
            memory_bank=self.config.get().memory.hindsight.bank_id,
            tools=["hindsight_recall", "hindsight_retain", "hindsight_reflect"],
            harness=self.config.get().harness.default,
        )
    
    async def attach_agent(self, agent: str, task_id: str) -> Any:
        """Attach to an existing agent session."""
        if task_id in self._active_agents:
            return self._active_agents[task_id]
        return None


class WorktreeTool:
    """Tool for managing git worktrees."""
    
    def __init__(self, worktree_manager: WorktreeManager):
        self.worktree_manager = worktree_manager
    
    async def execute(self, action: str, **kwargs) -> dict[str, Any]:
        """Execute a worktree action."""
        if action == "list":
            worktrees = self.worktree_manager.list_worktrees()
            return {
                "worktrees": [
                    {
                        "path": str(wt.path),
                        "branch": wt.branch,
                        "task_id": wt.task_id,
                        "agent": wt.agent_name,
                    }
                    for wt in worktrees
                ]
            }
        
        elif action == "create":
            task_id = kwargs.get("task_id", str(uuid.uuid4())[:8])
            agent = kwargs.get("agent", "backend")
            info = await self.worktree_manager.async_create_worktree(task_id, agent)
            return {"path": str(info.path), "branch": info.branch}
        
        elif action == "remove":
            task_id = kwargs.get("task_id")
            agent = kwargs.get("agent", "backend")
            force = kwargs.get("force", False)
            success = await self.worktree_manager.async_remove_worktree(task_id, agent, force)
            return {"success": success}
        
        elif action == "pr":
            task_id = kwargs.get("task_id")
            agent = kwargs.get("agent", "backend")
            title = kwargs.get("title", "Sweave task")
            body = kwargs.get("body", "")
            base = kwargs.get("base", "main")
            token = kwargs.get("token")
            url = await self.worktree_manager.async_create_pr(task_id, agent, title, body, base, token)
            return {"pr_url": url}
        
        return {"error": f"Unknown action: {action}"}


class MemoryTool:
    """Tool for interacting with long-term memory."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self.memory = MemoryFactory.create(config_manager.get().memory)
    
    def _get_bank_id(self, bank_id: str | None = None, scope: str = "project") -> str:
        """Resolve memory bank ID based on scope."""
        if bank_id:
            return bank_id
        return project_manager.get_memory_bank(scope)
    
    async def execute(self, action: str, **kwargs) -> dict[str, Any]:
        """Execute a memory action."""
        scope = kwargs.get("scope", "project")
        bank_id = self._get_bank_id(kwargs.get("bank_id"), scope)
        
        if action == "recall":
            query = kwargs.get("query", "")
            limit = kwargs.get("limit", 10)
            results = await self.memory.recall(query, bank_id, limit)
            return {
                "memories": [
                    {"content": m.content, "tags": m.tags, "metadata": m.metadata}
                    for m in results
                ]
            }
        
        elif action == "retain":
            content = kwargs.get("content", "")
            tags = kwargs.get("tags", [])
            result = await self.memory.retain(content, bank_id, tags)
            return {"result": result}
        
        elif action == "reflect":
            query = kwargs.get("query", "")
            result = await self.memory.reflect(query, bank_id)
            return {"reflection": result}
        
        return {"error": f"Unknown action: {action}"}