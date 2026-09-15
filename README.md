# Sweave

Multi-agent orchestration platform with persistent specialist agents.

## Overview

Sweave is a self-hosted meta-harness: it orchestrates persistent specialist agents (backend, frontend, reviewer) with isolated git worktrees, dynamic model routing, and long-term memory — across selectable agent runtimes. OpenCode is the currently supported external harness; `sweave-engine`, a zero-dependency Node sidecar, is built in and is the seed default (including the orchestrator), so OpenCode is optional. Agent specs follow the [Omnigent](https://github.com/omnigent-ai/omnigent) shape; the web UI is Sweave's own.

## Features

- **Persistent Specialist Agents** — Each agent runs on a selectable harness (built-in `sweave-engine` by default, OpenCode supported) with own context, memory, and workspace
- **Dynamic Model Routing** — Rules-based + LLM fallback routing with hot-reload
- **Git Worktree Isolation** — Each task/agent gets isolated worktree, auto-PR creation
- **Long-term Memory** — Configurable Hindsight backend (embedded, Docker, Cloud)
- **Meta-harness, not single-runtime** — Per-specialist harness selection (override > specialist > project > config); no silent fallback — an unresolved harness fails the turn loud
- **Web UI** — Model selector, worktree status, cost dashboard (via Omnigent)

## Quick Start

### Prerequisites

- Python 3.11+, Node.js on PATH (the default `sweave-engine`
  harness is a zero-dependency Node sidecar — no harness to install)

```bash
# Install dependencies
pip install -e ".[dev]"
```

### Provider auth (pick one)

```bash
# Default path — sweave-engine (no OpenCode needed).
# Export a key per provider, or add keys in Settings → Credentials
# (canonical store: ~/.sweave/credentials.json):
export OPENAI_API_KEY=...        # conventional <NAME>_API_KEY, or
export SWEAVE_ENGINE_KEY_FOO=... # engine-namespaced override

# Only if a specialist resolves to the opencode harness
# (see https://opencode.ai/docs/installation):
opencode auth login  # For each provider you use
# (also doubles as an auth source: Sweave can adopt its auth store once)
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
│  │  ORCHESTRATOR (sweave-engine default)                   │   │
│  │  • RouteTaskTool, DelegateTaskTool, WorktreeTool    │   │
│  │  • Dynamic routing via rules.yaml + LLM fallback    │   │
│  └─────────────────────────────────────────────────────┘   │
│                              │                               │
│        ┌───────────────────┼───────────────────┐           │
│        ▼                   ▼                   ▼           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│  │  BACKEND    │    │  FRONTEND   │    │  REVIEWER   │   │
│  │  SPECIALIST │    │  SPECIALIST │    │  SPECIALIST │   │
│  │  (engine    │    │  (engine    │    │  (engine    │   │
│  │   default)  │    │   default)  │    │   default)  │   │
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
- `harness` — Agent runtime default (`sweave-engine`; per-specialist / project / task-selectable to `opencode`)
- `memory` — Hindsight backend (embedded_slim, docker_full, docker_slim, cloud)
- `git` — Worktree and PR settings
- `models` — Model registry and rules paths
- `routing` — Routing rules (loaded from rules.yaml)

### models.yaml
Model registry with roles and aliases. GENERATED and untracked —
regenerate after clone (likewise `models.meta.json`):

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