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

A third managed piece is the top-level ``permission`` policy
(``_ensure_top_level_permission``): in headless ``serve`` mode any
check resolving to ``"ask"`` hangs forever, and
``external_directory`` (outside-cwd access) defaults to ``ask`` --
the orchestrator legitimately reads ``~/.sweave/*``, so it is set
to ``"allow"``. This is deliberately NOT a blanket allow: danger
gates stay in the per-agent profiles
(``runtime/agent_permission.py``).

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

# Headless hang classes closed at the top level (2026-09-10
# stream-probe incident). In ``opencode serve`` there is no UI to
# answer an approval prompt, so ANY permission check resolving to
# ``"ask"`` waits forever: the tool sits at ``status=running``,
# the session freezes, and nothing -- no part, no error -- ever
# surfaces (two consecutive 5-minute ReadTimeouts on trivial
# outside-cwd reads proved it).
#
# ``external_directory`` (fires when a path resolves outside the
# session cwd) defaults to ``ask`` and is the one that bit us: the
# orchestrator legitimately reads ``~/.sweave/*`` (global agents,
# traces), so it must resolve deterministically.
#
# M1.12 (user rulings 2026-09-10):
# * The blanket ``"allow"`` was a TRANSITION (ruling 3: it stays
#   until the ask-handling flow works, then flips). The flip lives
#   in ``EXTERNAL_DIRECTORY_CATCH_ALL`` -- one constant, step 4
#   changes it to ``"ask"`` and Sweave's permission-question flow
#   becomes the ask UI.
# * Scoped render (:func:`render_external_directory`): the catch-all
#   FIRST, then the specifics (opencode pattern matching is
#   last-match-wins), then the built-in + user-declared roots as
#   ``allow``.
# * The danger gates stay in the per-agent ``bash``/``task``/
#   ``question`` profiles in ``runtime/agent_permission.py``.
# Deliberately NOT a blanket ``"*": "allow"``: every other class
# keeps its default until one proves it hangs headless (then it
# gets its own entry + comment, never a wildcard).
EXTERNAL_DIRECTORY_CATCH_ALL = "allow"

_BUILTIN_ROOT_MARKER = "~/.sweave"


def _root_globs(project_dir: Path, permission_roots: Any) -> list[Path]:
    """Expand the scoped-root list (built-ins + user-declared).

    Built-ins (ruling 2): the project's worktree base (``.
    worktrees/**`` under the project dir -- covers specialist
    worktrees too) and the global ``~/.sweave`` (required ruling:
    the hung reads were global ``agents.yaml``). User roots
    (human-declared only) expand ``~`` and empty entries are
    dropped with a warning; duplicates collapse.
    """
    roots: list[Path] = []
    roots.append(Path(os.path.expanduser(_BUILTIN_ROOT_MARKER)))
    roots.append(Path(project_dir) / ".worktrees")
    seen: set[str] = set()
    if isinstance(permission_roots, (list, tuple)):
        for raw in permission_roots:
            text = str(raw).strip()
            if not text:
                continue
            expanded = Path(os.path.expanduser(text))
            try:
                resolved = expanded.resolve()
            except OSError:
                resolved = expanded
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            roots.append(resolved)
    return roots


def render_external_directory(
    project_dir: Path,
    permission_roots: Any = None,
) -> dict[str, Any]:
    """Render the scoped ``external_directory`` permission map.

    Opencode pattern matching is last-match-wins (upstream docs),
    so the catch-all is FIRST and specifics AFTER it. ``cwd``
    subfolders are implicitly allowed by opencode itself (the check
    only fires outside the session cwd) -- every value here is
    about the outside-cwd space.
    """
    pattern_map: dict[str, Any] = {"*": EXTERNAL_DIRECTORY_CATCH_ALL}
    for root in _root_globs(project_dir, permission_roots):
        glob = str(root).replace("\\", "/") + "/**"
        pattern_map[glob] = "allow"
    return pattern_map


def _ensure_top_level_permission(
    existing: dict[str, Any],
    project_dir: Path,
    permission_roots: Any = None,
) -> bool:
    """Merge the managed top-level ``permission`` policy.

    Returns True iff the caller should persist (we changed
    something). Ownership rules mirror the agent map: absent ->
    write managed block; present-with-marker -> refresh our keys,
    preserve user keys; present-without-marker (fully user-owned)
    -> leave alone + warn (an ``ask`` default in there will hang
    the headless serve; that warning is the most we can do without
    overwriting someone's security config).
    """
    current = existing.get("permission", None)
    if current is None:
        existing["permission"] = {
            **_sweave_managed_permission(project_dir, permission_roots),
            _MANAGED_KEY: _MANAGED_VALUE,
        }
        return True
    if not isinstance(current, dict):
        logger.warning(
            "ensure_mcp_config: top-level 'permission' has an unexpected "
            "shape (%s); leaving it alone (an 'ask' default will hang "
            "headless serves)",
            type(current).__name__,
        )
        return False
    if not current.get(_MANAGED_KEY):
        if "external_directory" not in current:
            logger.warning(
                "ensure_mcp_config: user-owned top-level 'permission' has no "
                "'external_directory' entry: the opencode default ('ask') "
                "hangs headless serves forever on outside-cwd access. "
                "Consider setting it explicitly."
            )
        return False
    changed = any(
        current.get(key) != value
        for key, value in _sweave_managed_permission(
            project_dir, permission_roots
        ).items()
    )
    if changed:
        for key, value in _sweave_managed_permission(
            project_dir, permission_roots
        ).items():
            current[key] = value
    return changed


def _sweave_managed_permission(
    project_dir: Path,
    permission_roots: Any = None,
) -> dict[str, Any]:
    """The managed top-level ``permission`` block (minus marker)."""
    return {
        "external_directory": render_external_directory(
            project_dir, permission_roots
        ),
    }

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
    permission_roots: Any = None,
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
    * ``permission_roots`` -- M1.12: user-declared outside-cwd
      roots (human-declared only, ruling 2026-09-10). Fed into the
      scoped ``external_directory`` render; ``None`` = built-in
      roots only (cwd subfolders + worktrees + ``~/.sweave``).

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
        # Headless permission policy (never unhandled-ask): merged
        # with the same ownership rules as the agent map (user-owned
        # blocks are never overwritten; see
        # _ensure_top_level_permission). The M1.12 scoped render
        # needs the project dir for the worktree-root glob.
        _ensure_top_level_permission(existing, project_dir, permission_roots)
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
