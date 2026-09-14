# Sweave

Multi-agent orchestration platform with persistent specialist agents.

## Overview

Sweave is a self-hosted orchestration platform that manages persistent specialist agents (backend, frontend, reviewer) with isolated git worktrees, dynamic model routing, and long-term memory. Built on [Omnigent](https://github.com/omnigent-ai/omnigent) and [OpenCode](https://opencode.ai).

## Features

- **Persistent Specialist Agents** — Each agent is a full OpenCode instance with own context, memory, and workspace
- **Dynamic Model Routing** — Rules-based + LLM fallback routing with hot-reload
- **Git Worktree Isolation** — Each task/agent gets isolated worktree, auto-PR creation
- **Long-term Memory** — Configurable Hindsight backend (embedded, Docker, Cloud)
- **Harness Agnostic** — OpenCode first, extensible to Claude Code, Codex, ACP
- **Web UI** — Model selector, worktree status, cost dashboard (via Omnigent)

## Quick Start

### Prerequisites

```bash
# Install dependencies
pip install -e ".[dev]"

# Install OpenCode (for provider auth)
# See: https://opencode.ai/docs/installation

# Configure providers
opencode auth login  # For each provider you use
```

### Configuration

```bash
# Copy and customize config (config.yaml is a live working file, not tracked)
cp config.example.yaml config.yaml

# Generate models.yaml from models.dev
python scripts/generate_models.py

# Initialize memory (embedded + OpenAI embeddings)
python scripts/setup_hindsight.py init --mode embedded_slim --openai-key $OPENAI_API_KEY
```

### Run

```bash
# Run a task
sweave run "Build a REST API with JWT authentication"

# Check routing
sweave route "Create a React dashboard component"

# Manage models
sweave models list
sweave models set backend deepseek-coder

# Manage worktrees
sweave worktree list
sweave worktree pr task-123:backend

# Memory
sweave memory recall "What did we decide about auth?"
sweave memory reflect "Summarize our API decisions"

# System check
sweave doctor
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      SWEAVE SERVER                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  ORCHESTRATOR (OpenCode)                            │   │
│  │  • RouteTaskTool, DelegateTaskTool, WorktreeTool    │   │
│  │  • Dynamic routing via rules.yaml + LLM fallback    │   │
│  └─────────────────────────────────────────────────────┘   │
│                              │                               │
│        ┌───────────────────┼───────────────────┐           │
│        ▼                   ▼                   ▼           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│  │  BACKEND    │    │  FRONTEND   │    │  REVIEWER   │   │
│  │  SPECIALIST │    │  SPECIALIST │    │  SPECIALIST │   │
│  │  (OpenCode) │    │  (OpenCode) │    │  (OpenCode) │   │
│  │             │    │             │    │             │   │
│  │ • Own ctx   │    │ • Own ctx   │    │ • Own ctx   │   │
│  │ • Worktree  │    │ • Worktree  │    │ • Worktree  │   │
│  │ • Memory    │    │ • Memory    │    │ • Memory    │   │
│  └─────────────┘    └─────────────┘    └─────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

### config.yaml
Main configuration file (live working artifact — untracked; user
defaults land here at runtime). See `config.example.yaml` for all options.

Key sections:
- `server` — HTTP server settings
- `harness` — Agent runtime (OpenCode, future: Claude Code, Codex)
- `memory` — Hindsight backend (embedded_slim, docker_full, docker_slim, cloud)
- `git` — Worktree and PR settings
- `models` — Model registry and rules paths
- `routing` — Routing rules (loaded from rules.yaml)

### models.yaml
Model registry with roles and aliases. Generated from models.dev:

```bash
python scripts/generate_models.py
```

### rules.yaml
Routing rules for automatic agent selection:

```yaml
routes:
  - pattern: "backend|api|database"
    agent: "backend"
    model: "{{models.backend.default}}"
fallback: "llm"
```

## Memory Backends

| Mode | Description | Requirements |
|------|-------------|--------------|
| `embedded_slim` | Python embedded, OpenAI embeddings | `pip install hindsight-all-slim`, OpenAI API key |
| `docker_full` | Docker with local embeddings | Docker, ~2GB RAM |
| `docker_slim` | Docker with external embeddings | Docker, OpenAI API key, ~1GB RAM |
| `cloud` | Hindsight Cloud | API key |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .
mypy sweave
```

## License

Apache-2.0