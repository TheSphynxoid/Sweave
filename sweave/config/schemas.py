from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessConfig(BaseModel):
    """Configuration for a specific harness."""
    command: str = "opencode"
    serve_args: list[str] = Field(default_factory=lambda: ["--port", "0"])
    env: dict[str, str] = Field(default_factory=dict)


class HarnessSettings(BaseModel):
    """Harness configuration.

    The default is the built-in engine: a fresh clone runs with
    nothing to install (no external harness binary). Select
    ``"opencode"`` per specialist / project / task when you want it.
    """
    default: str = "sweave-engine"
    opencode: HarnessConfig = Field(default_factory=HarnessConfig)
    # Future: claude_code, codex, acp_custom


class HindsightConfig(BaseModel):
    """Hindsight memory configuration."""
    mode: Literal["embedded_slim", "docker_full", "docker_slim", "cloud"] = "embedded_slim"
    api_key: str | None = None
    api_url: HttpUrl | None = None
    embeddings_provider: Literal["openai", "cohere", "tei", "local"] = "openai"
    embeddings_api_key: str | None = None
    embeddings_model: str = "text-embedding-3-small"
    reranker_provider: Literal["openai", "cohere", "tei", "local"] = "openai"
    reranker_api_key: str | None = None
    bank_id: str = "project-memory"


class MemoryConfig(BaseModel):
    """Memory backend configuration."""
    backend: Literal["hindsight", "none"] = "hindsight"
    hindsight: HindsightConfig = Field(default_factory=HindsightConfig)


class GitConfig(BaseModel):
    """Git/worktree configuration."""
    provider: Literal["github", "gitlab", "local"] = "github"
    token: str | None = None
    worktree_base: str = ".worktrees"
    auto_pr: bool = True
    pr_base_branch: str = "main"


class ModelAlias(BaseModel):
    """Model alias with metadata."""
    name: str
    provider: str = "opencode"
    description: str | None = None


class ModelsConfig(BaseModel):
    """Models configuration.

    Single global model list grouped by provider (no per-role lists).
    ``default`` is the qualified ``provider/model`` used for the
    orchestrator and any specialist without an explicit
    ``current_model``. Persisted in models.yaml under ``models:``.
    """
    registry_path: str = "models.yaml"
    rules_path: str = "rules.yaml"
    hot_reload: bool = True
    providers: dict[str, list[str]] = Field(default_factory=dict)
    default: str | None = None


class RoutingRule(BaseModel):
    """Single routing rule."""
    pattern: str
    agent: str
    model: str | None = None


class RoutingConfig(BaseModel):
    """Routing rules configuration."""
    routes: list[RoutingRule] = Field(default_factory=list)
    fallback: Literal["llm", "first"] = "llm"
    # M1.6: chain budget (coordination tokens per deferral chain).
    # Counts only orchestrator turns + defer payloads + inter-specialist
    # result summaries -- specialist internal work is opaque by
    # design (the opencode v2 stream exposes no per-turn token count).
    # Plan ruling 2026-08-30: default 200K.
    chain_budget: int = 200_000
    # Depth cap on the deferral chain. Plan ruling: orchestrator depth
    # is 0; a child has depth 1; depth 2 = grandchild (orchestrator ->
    # specialist -> defer -> orchestrator -> peer). Plan ruling
    # 2026-08-30: default 2.
    max_depth: int = 2
    # Per-turn wall-clock cap (seconds) for a managed specialist /
    # orchestrator agent turn (JobRunner._bounded_turn + ChatLoop).
    # M1.3 plan default was 900s; raised to 1800s (30 min, ruling
    # 2026-09-10) because real agentic turns (large worktree edits,
    # extended thinking) routinely outlive 15 min, and because the
    # M1.12 shielded turn-cap extension can still stretch the total
    # (bounded x3) — the base budget should not be the bottleneck.
    #
    # Bound: 0 < turn_timeout_s <= 14_400 (4 hours). Justification:
    # (a) the M1.12 beacon machinery already multiplies the effective
    # wall-clock by up to (extensions+1)x, so the base budget must
    # stay a single-turn cap, not a session cap; (b) each turn holds
    # a per-specialist serve process alive — beyond ~4h a "turn" is a
    # wedged serve, and leaked serves compound forever; (c) 4h keeps
    # the boutique single-digit-hour ceiling aligned with opencode's
    # own practical turn sizes. Anything <= 0 makes wait_for fire
    # instantly (turn can never succeed); anything > 4h is
    # almost certainly a typo (hours entered as seconds).
    turn_timeout_s: float = 1800.0

    @field_validator("turn_timeout_s")
    @classmethod
    def _validate_turn_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("turn_timeout_s must be > 0 (seconds)")
        if v > 14_400:
            raise ValueError(
                "turn_timeout_s must be <= 14400s (4h): beyond that a "
                "turn is a wedged serve, not a turn"
            )
        return v

    # Provider-call retries per engine turn (user ruling: wait and
    # retry like opencode, at least 3). Counts retries AFTER the first
    # provider attempt (opencode RETRY_MAX_RETRIES semantics: 3 → up
    # to 4 tries). Applies to transient failures only (429 / 5xx /
    # rate-limit / network-down / timeouts); auth, bad-request,
    # quota-exhausted and context-overflow never retry. Lives beside
    # turn_timeout_s: same per-turn scope, same hot-reload path. The
    # sidecar defaults to 3 when a turn carries no value (specialist
    # turns), so this knob binds the chat path explicitly.
    turn_retries: int = 3

    @field_validator("turn_retries")
    @classmethod
    def _validate_turn_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError("turn_retries must be >= 0 (0 disables retry)")
        if v > 10:
            raise ValueError(
                "turn_retries must be <= 10: beyond that a turn is "
                "hammering a dead provider, not recovering"
            )
        return v


class ServerConfig(BaseModel):
    """Server configuration."""
    host: str = "127.0.0.1"
    port: int = 8080


class SweaveConfig(BaseSettings):
    """Main Sweave configuration."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    harness: HarnessSettings = Field(default_factory=HarnessSettings)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> SweaveConfig:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    def to_yaml(self, path: Path) -> None:
        import yaml
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(exclude_none=True), f, sort_keys=False)


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
    harness: str = "sweave-engine"
