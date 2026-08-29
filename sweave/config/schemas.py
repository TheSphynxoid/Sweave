from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessConfig(BaseModel):
    """Configuration for a specific harness."""
    command: str = "opencode"
    serve_args: list[str] = Field(default_factory=lambda: ["--port", "0"])
    env: dict[str, str] = Field(default_factory=dict)


class HarnessSettings(BaseModel):
    """Harness configuration."""
    default: str = "opencode"
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


class ModelRoleConfig(BaseModel):
    """Model configuration for a specific role."""
    default: str
    aliases: list[str] = Field(default_factory=list)
    provider: str = "opencode"


class ModelsConfig(BaseModel):
    """Models configuration."""
    registry_path: str = "models.yaml"
    rules_path: str = "rules.yaml"
    hot_reload: bool = True
    roles: dict[str, ModelRoleConfig] = Field(default_factory=dict)


class RoutingRule(BaseModel):
    """Single routing rule."""
    pattern: str
    agent: str
    model: str | None = None


class RoutingConfig(BaseModel):
    """Routing rules configuration."""
    routes: list[RoutingRule] = Field(default_factory=list)
    fallback: Literal["llm", "first"] = "llm"


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
        with open(path) as f:
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
    harness: str = "opencode"