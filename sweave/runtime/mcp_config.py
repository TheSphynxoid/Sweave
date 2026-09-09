"""Per-project opencode MCP config plumbing (M1.6 step 3).

When a project is activated, the orchestrator's opencode serve
(launched with that project's cwd) needs to discover the sweave MCP
server. opencode's per-project config precedence (step 0) supports
this: a ``opencode.json`` in the project root (or in ``.opencode/``)
with an ``mcp.sweave = {type: "local", command: [...], ...}`` entry
makes the MCP server visible to the orchestrator's session.

The same write also renders the managed opencode-native ``agent``
map (``sweave-orchestrator`` / ``sweave-specialist``): custom
prompts (sourced from ``sweave/agents/*/config.yaml``) plus the
per-role permission profiles. SpecialistRuntime pins every message
to the matching agent (``body["agent"]``), so tool gating is
enforced by opencode itself -- specialists cannot see the sweave
MCP tools even though the serve discovers the shared ``mcp.sweave``
block via upward config resolution.

This module writes that file **idempotently** with a versioned
marker, so re-activation is a no-op and a user-edited config isn't
clobbered on every activate. The marker is the key
``_sweave_managed`` inside the ``mcp.sweave`` entry (and inside each
managed ``agent`` entry); if the marker is absent (user wrote their
own block) we leave the file alone.

Token + activation: the shared MCP token is read from
``~/.sweave/mcp_token`` and embedded in the spawned process's
``environment`` block (one of the opencode MCP env keys). The
``SWEAVE_DELEGATION_ID`` env var is also injected for the
orchestrator's prompt (M1.6 step 3's prompt contract); the
orchestrator's opencode session sets it on each turn so the
``defer`` tool can pass the right ``caller_delegation_id``.

The flag ``M1.6_DISABLE_MCP_PLUMBING=1`` disables the write
entirely (handy for tests; production never sets it).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Marker key inside the mcp.sweave entry. Presence of this key (set
# to True) means sweave wrote this block; absence means the user
# wrote their own -- we don't touch it.
_MANAGED_KEY = "_sweave_managed"
_MANAGED_VALUE = True

# The opencode version we tested the schema with (M1.6 step 0 probe).
# If the user's opencode is older / newer and the schema changes,
# the marker still protects us; the worst case is a no-op config
# write + the user seeing a stale entry.
_OPENCODE_CONFIG_FILENAME = "opencode.json"


def _opencode_config_path(project_dir: Path) -> Path:
    """Where the per-project opencode.json lives.

    We use the project root, NOT a ``.opencode/`` subdir -- the
    latter is for agents/commands/plugins, while ``opencode.json``
    in the project root is the standard per-project config location
    (opencode 1.18 docs).
    """
    return project_dir / _OPENCODE_CONFIG_FILENAME


def _sweave_mcp_entry(token: str, env_token_var: str) -> dict[str, Any]:
    """Build the ``mcp.sweave`` entry that gets merged into the
    project's ``opencode.json``.

    The command array uses :func:`sweave.platform.pythonw_executable`
    (windowless; opencode spawns this process itself so our
    CREATE_NO_WINDOW can't cover it). ``cwd`` pins the Sweave package
    root: ``python -m sweave.mcp`` only resolves where the package is
    importable, and sweave runs from source (not pip-installed), so
    any non-repo project would otherwise get a crashing MCP server.
    The ``cwd`` is the Sweave installation, NOT the project -- the
    server is project-agnostic (project comes from SWEAVE_PORT/HOST
    + the API calls it makes).

    NOTE: opencode strips unknown keys inside an MCP server entry
    (``additionalProperties: false``) -- tool permissions do NOT
    belong here. They live on the managed agents (see
    :func:`_sweave_agent_map`), which is also why the M1.9
    ``permission`` key once nested here never took effect.
    """
    from sweave.platform import pythonw_executable

    return {
        "type": "local",
        "command": [pythonw_executable(), "-m", "sweave.mcp"],
        "cwd": str(_sweave_package_root()),
        "environment": {
            "SWEAVE_MCP_TOKEN": f"{{env:{env_token_var}}}",
        },
        "enabled": True,
        "timeout": 30000,
        # Marker: presence of this key means sweave wrote the block.
        # Re-runs are no-ops; user-edited blocks (no marker) are
        # left alone.
        _MANAGED_KEY: _MANAGED_VALUE,
    }


def _sweave_package_root() -> Path:
    """Directory containing the ``sweave`` package (repo root in
    source runs). The MCP server's ``cwd`` so ``python -m
    sweave.mcp`` resolves regardless of the project directory."""
    return Path(__file__).resolve().parent.parent.parent


# Opencode-native agent names managed by Sweave. Pinned per message
# by SpecialistRuntime (``body["agent"]``); never user-facing.
ORCHESTRATOR_AGENT_NAME = "sweave-orchestrator"
SPECIALIST_AGENT_NAME = "sweave-specialist"

# Generic specialist charter. Per-specialist flavor (backend /
# frontend / reviewer / custom role prompts) is still delivered as
# the session's one-off system message; this charter is the
# opencode-side enforcement layer (identity + tool gating), rendered
# once per project activation.
SPECIALIST_AGENT_CHARTER = """You are a Sweave specialist. You implement delegated work inside your task worktree and commit it there.

- Do the work yourself. You have no delegation tools: never attempt to defer, list, or escalate through any sweave surface. If you are blocked or need a human decision, state it clearly in your final summary.
- Commit your work in your branch. Never merge into the base branch and never touch files outside your worktree.
- End your turn with a concise summary of what changed plus test evidence."""


def _sweave_agent_map() -> dict[str, Any]:
    """Build the managed ``agent`` map for the project's opencode.json.

    Source of truth for prompts is ``sweave/agents/*/config.yaml``
    (via the loader): the orchestrator agent carries the full
    orchestrator prompt, so the opencode-native agent and the
    legacy one-off system message can never drift. Permissions come
    from ``runtime/agent_permission.py``.
    """
    from sweave.agents.loader import load_seed_agents
    from sweave.runtime.agent_permission import render_agent_permission_profile

    seeds = load_seed_agents()
    orch = seeds.get("orchestrator")
    return {
        ORCHESTRATOR_AGENT_NAME: {
            "description": (orch.description if orch else "") or "Sweave orchestrator: decomposes work and delegates to specialists.",
            "mode": "primary",
            "prompt": (orch.prompt if orch else "") or "",
            "permission": render_agent_permission_profile(is_orchestrator=True),
            _MANAGED_KEY: _MANAGED_VALUE,
        },
        SPECIALIST_AGENT_NAME: {
            "description": "Sweave specialist: implements delegated work in its worktree.",
            "mode": "primary",
            "prompt": SPECIALIST_AGENT_CHARTER,
            "permission": render_agent_permission_profile(is_orchestrator=False),
            _MANAGED_KEY: _MANAGED_VALUE,
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write the file atomically (write tmp + os.replace)."""
    import tempfile

    fd, tmp = tempfile.mkstemp(
        prefix=".opencode.", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_mcp_config(
    project_dir: Path,
    *,
    token_env_var: str = "SWEAVE_MCP_TOKEN",
    dry_run: bool | None = None,
) -> dict[str, Any]:
    """Idempotently write the per-project ``opencode.json`` with the
    sweave MCP server block. Returns the final merged config so the
    caller can log / assert it.

    Parameters:
    * ``project_dir`` -- the project's filesystem root
    * ``token_env_var`` -- the env var name where opencode looks up
      the MCP token at spawn time (default ``SWEAVE_MCP_TOKEN``; the
      AppState lifespan exports the token from
      ``~/.sweave/mcp_token`` into this env var so the subprocess
      reads it).
    * ``dry_run`` -- if True, do not write; return the would-be
      config. If None, reads the ``M1.6_DISABLE_MCP_PLUMBING`` env
      var (the test/CI kill switch).

    The function is **idempotent**: re-calling it with the same
    inputs produces the same file content. A user-edited config
    that lacks the ``_sweave_managed`` marker is left alone.
    """
    if dry_run is None:
        dry_run = os.environ.get("M1.6_DISABLE_MCP_PLUMBING") == "1"

    path = _opencode_config_path(project_dir)
    existing = _read_json(path)

    mcp_block = existing.get("mcp", {})
    if not isinstance(mcp_block, dict):
        mcp_block = {}
    existing_sweave = mcp_block.get("sweave", {})

    # If a sweave entry exists but is NOT managed by us, leave it
    # alone (user override). If it IS managed, refresh in place.
    if existing_sweave and not existing_sweave.get(_MANAGED_KEY):
        logger.info(
            "ensure_mcp_config: project %s has a user-written 'mcp.sweave' block; skipping",
            project_dir,
        )
        return existing
    if not dry_run:
        mcp_block["sweave"] = _sweave_mcp_entry(
            token=existing_sweave.get("environment", {}).get(
                "SWEAVE_MCP_TOKEN", ""
            ) or "{env:SWEAVE_MCP_TOKEN}",
            env_token_var=token_env_var,
        )
        existing["mcp"] = mcp_block
        # Managed opencode-native agents (same marker convention per
        # agent name: present-without-marker = user-owned, left
        # alone; otherwise refreshed from the YAML specs so prompt
        # edits land on the next activation).
        agent_block = existing.get("agent", {})
        if not isinstance(agent_block, dict):
            agent_block = {}
        for name, rendered in _sweave_agent_map().items():
            current = agent_block.get(name, {})
            if current and not (isinstance(current, dict) and current.get(_MANAGED_KEY)):
                logger.info(
                    "ensure_mcp_config: project %s has a user-written 'agent.%s' block; skipping",
                    project_dir,
                    name,
                )
                continue
            agent_block[name] = rendered
        existing["agent"] = agent_block
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(path, existing)
            logger.info("ensure_mcp_config: wrote %s", path)
        except OSError as e:
            logger.warning("ensure_mcp_config: write failed for %s: %s", path, e)
    return existing


def has_managed_sweave_block(project_dir: Path) -> bool:
    """True iff the project's opencode.json carries the sweave-managed
    block. Used by tests + the activate-project endpoint to skip
    re-writes (the ensure_mcp_config path is already idempotent;
    this is a fast read-side check)."""
    existing = _read_json(_opencode_config_path(project_dir))
    mcp_block = existing.get("mcp", {})
    if not isinstance(mcp_block, dict):
        return False
    sweave = mcp_block.get("sweave", {})
    return isinstance(sweave, dict) and bool(sweave.get(_MANAGED_KEY))
