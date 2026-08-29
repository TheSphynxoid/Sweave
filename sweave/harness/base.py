from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Any
import asyncio
import json


@dataclass
class AgentSpec:
    """Specification for spawning a specialist agent."""
    name: str
    role: str
    model: str
    system_prompt: str
    worktree_path: Path
    memory_bank: str
    tools: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    harness: str = "opencode"


@dataclass
class Message:
    """Message sent to/from an agent."""
    type: str  # "user", "assistant", "tool", "system"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result from agent execution."""
    success: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AgentProcess(Protocol):
    """Handle to a running agent process."""
    pid: int
    session_id: str
    spec: AgentSpec
    
    async def send(self, message: Message) -> AgentResult:
        """Send a message to the agent."""
        ...
    
    async def terminate(self) -> None:
        """Terminate the agent process."""
        ...
    
    async def wait(self) -> AgentResult:
        """Wait for agent to complete."""
        ...


class Harness(ABC):
    """Abstract base class for agent harnesses."""
    
    name: str = "base"
    
    @abstractmethod
    async def spawn(self, spec: AgentSpec) -> AgentProcess:
        """Spawn a new agent process."""
        ...
    
    @abstractmethod
    async def attach(self, session_id: str, spec: AgentSpec) -> AgentProcess:
        """Attach to an existing agent session."""
        ...
    
    @abstractmethod
    def get_default_tools(self) -> list[str]:
        """Get default tools provided by this harness."""
        ...
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if harness is available."""
        ...


class HarnessRegistry:
    """Registry of available harnesses."""
    
    def __init__(self):
        self._harnesses: dict[str, Harness] = {}
    
    def register(self, harness: Harness) -> None:
        self._harnesses[harness.name] = harness
    
    def get(self, name: str) -> Harness | None:
        return self._harnesses.get(name)
    
    def get_default(self) -> Harness:
        return self._harnesses.get("opencode") or next(iter(self._harnesses.values()))
    
    def list(self) -> list[str]:
        return list(self._harnesses.keys())


# Global registry
harness_registry = HarnessRegistry()