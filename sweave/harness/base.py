from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Any, TypedDict
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


class ModelRef(TypedDict, total=False):
    """Model reference for the v2 opencode body (M1.3 K-revised; M1.4+M1.5 contract).

    The harness contract type for model identity. Lives in
    :mod:`sweave.harness.base` so all harnesses (R3: claude, codex, ...)
    and the runtime can speak the same shape. The previous home in
    :mod:`sweave.runtime.specialist_store` is a re-export for backward
    compatibility — no API break.

    The user's ``opencode.json`` may declare multiple providers (ollama,
    gmi/gmicloud, zai, openrouter, opencode, etc.). The v2 protocol
    requires the ``body["model"]`` field to be either ``null`` or a
    structured ``{providerID, modelID}`` object — bare model names are
    rejected with 400 (per M1.3 step 0 probe 5b).

    * ``provider`` — the opencode provider id (e.g. ``ollama``, ``gmi``,
      ``zai``, ``opencode``). When None, the harness falls back to the
      unqualified-name path AND the opencode serve's own default
      provider resolution; this is the legacy M1.0 path and may 400 on
      multi-provider configs (warning fires once per session).
    * ``model_id`` — the model id within the provider (e.g.
      ``qwen3:8b``, ``MiniMaxAI/MiniMax-M3``, ``glm-5.3``).
    * ``variant`` — optional opencode model variant (e.g. ``none``,
      ``low``, ``medium``, ``high``, ``max``). Variants select a
      reasoning-effort preset per model (the serve advertises them
      per model on ``GET /config/providers``). Passed through to
      ``body["model"]["variant"]`` when set; omitted otherwise so
      the provider default applies. String form appends
      ``+variant`` (``"provider/model+variant"``); the ``+`` is
      chosen because model ids never contain it (unlike ``/``,
      ``:`` and ``@``, which all occur in real ids).
    """
    provider: str
    model_id: str
    variant: str


def model_ref_to_wire(ref: ModelRef | None) -> "dict[str, str] | None":
    """Convert a :class:`ModelRef` to the v2 ``body["model"]`` shape.

    Returns the structured ``{providerID, modelID[, variant]}`` dict
    when provider + model_id are set (the v2 protocol's expected
    shape; ``variant`` rides along when the ref carries one), or
    ``None`` when the ref is empty / incomplete (caller falls back
    to the unqualified-name path or null).
    """
    if ref is None:
        return None
    provider = ref.get("provider")
    model_id = ref.get("model_id")
    if not provider or not model_id:
        return None
    wire: dict[str, str] = {"providerID": provider, "modelID": model_id}
    variant = ref.get("variant")
    if variant:
        wire["variant"] = variant
    return wire


@dataclass
class Message:
    """Message sent to/from an agent.

    ``model`` (M1.4+M1.5 step 1) overrides the per-process default in
    :attr:`AgentSpec.model` for this one message. Adapters (R3: claude,
    codex) implement per-invocation model flags the same way — the
    runtime always resolves the model at submit time, and the harness
    honors the per-message override when present. Per-message beats
    spawn-time.
    """
    type: str  # "user", "assistant", "tool", "system"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    model: ModelRef | None = None


@dataclass
class AgentResult:
    """Result from agent execution."""
    success: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AgentProcess(Protocol):
    """Handle to a running agent process.

    **Model-per-request is the harness contract (M1.4+M1.5 step 1).**
    Adapters (R3: claude/codex) implement per-invocation flags the same
    way OpenCode does: ``send(message)`` builds the request body from
    ``message.model`` when set, falling back to ``spec.model``. The
    runtime resolves the model at submit time so a switch (e.g. via
    ``PUT /api/specialists/{name}/model``) takes effect on the NEXT
    delegation, never mid-task.
    """
    pid: int
    session_id: str
    spec: AgentSpec

    async def send(self, message: Message) -> AgentResult:
        """Send a message to the agent.

        ``message.model`` (a :class:`ModelRef`) wins over
        ``self.spec.model`` when set; adapters translate that to their
        per-invocation flag (OpenCode: ``body["model"]``; claude: the
        ``--model`` flag; codex: the ``-m`` arg).
        """
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