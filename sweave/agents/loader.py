from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).resolve().parent


@dataclass
class AgentDefinition:
    """A specialist agent definition loaded from an Omnigent-spec config.yaml."""

    role: str
    name: str
    description: str = ""
    prompt: str = ""
    model_template: str | None = None
    harness: str = "opencode"
    worktree_cwd: str | None = None
    tools: list[str] = field(default_factory=list)


_cache: dict[str, AgentDefinition] | None = None


def load_seed_agents(
    agents_dir: Path | None = None, refresh: bool = False
) -> dict[str, AgentDefinition]:
    """Load agent definitions from ``agents/*/config.yaml`` (Omnigent-spec shape).

    Keyed by directory name (the role). Malformed entries are skipped with a
    warning; a missing directory yields an empty dict so callers can fall back.
    """
    global _cache
    if _cache is not None and not refresh and agents_dir is None:
        return _cache

    base = agents_dir or AGENTS_DIR
    defs: dict[str, AgentDefinition] = {}
    if not base.is_dir():
        logger.warning("Agents directory not found: %s", base)
        return defs

    for child in sorted(base.iterdir()):
        cfg_path = child / "config.yaml"
        if not child.is_dir() or not cfg_path.is_file():
            continue
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Skipping agent config %s: %s", cfg_path, e)
            continue

        executor = data.get("executor") or {}
        exec_cfg = executor.get("config") or {}
        os_env = data.get("os_env") or {}
        tools = (data.get("tools") or {}).get("builtins") or []

        defs[child.name] = AgentDefinition(
            role=child.name,
            name=data.get("name") or child.name,
            description=data.get("description") or "",
            prompt=(data.get("prompt") or "").strip(),
            model_template=executor.get("model"),
            harness=exec_cfg.get("harness") or "opencode",
            worktree_cwd=os_env.get("cwd"),
            tools=[str(t) for t in tools],
        )

    if agents_dir is None:
        _cache = defs
    return defs


def get_agent_definition(role: str, agents_dir: Path | None = None) -> AgentDefinition | None:
    """Get a seed agent definition by role, or None if unknown."""
    return load_seed_agents(agents_dir).get(role)
