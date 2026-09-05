"""Specialist permission profiles (M1.9 step 2 hardening).

The M1.9 hardening bundle closes two bypass paths:

* **Native opencode subagent spawning.** Specialists' opencode
  sessions default to allowing ``task:`` (the native subagent tool
  inside opencode), which would let a specialist spawn a native
  sub-subagent outside the DelegationManager's chain. Setting
  ``permission.task: deny`` on the specialist agent's config
  closes that bypass. The deferral path remains the only way to
  spawn work (via MCP, with depth / loop / budget enforcement).

* **Orchestrator git mutation.** The orchestrator is the user-facing
  conversational supervisor; it must not commit / merge / push to
  the user's checkout. The orchestrator's session config denies
  ``bash: `` patterns matching ``git commit``, ``git merge``,
  ``git push``, ``gh pr merge``. Specialists (per the commit-
  authority map, 2026-09-04) commit freely in their disposable
  branches; only the orchestrator is denied.

The profile shape is the opencode per-agent permission block:

    {"task": "deny", "bash": {"*": ["git commit*", ...]}}

Used by ``runtime/mcp_config.py`` to merge specialist-specific
permission profiles into the per-project opencode.json.
"""

from __future__ import annotations

from typing import Any


# The orchestrator's git-mutation deny list. Globs are matched by
# opencode's permission system; the explicit list below covers every
# git mutation the orchestrator must NOT perform on the user's
# checkout.
ORCHESTRATOR_BASH_DENY: tuple[str, ...] = (
    "git commit*",
    "git merge*",
    "git push*",
    "gh pr merge*",
    "git rebase*",
    "git reset --hard*",
)


def render_agent_permission_profile(*, is_orchestrator: bool) -> dict[str, Any]:
    """Render the per-agent permission block for opencode.

    Returns the permission profile as ``{"task": ..., "bash": ...}``.

    Specialists (non-orchestrator):
        ``{"task": "deny", "bash": {"*": []}}`` -- deny the native
        subagent tool; no git deny (specialists commit freely in
        their disposable branches per the commit-authority map).

    Orchestrator:
        ``{"task": "deny", "bash": {"*": <ORCHESTRATOR_BASH_DENY>}}``
        -- deny the native subagent tool AND the git-mutation bash
        patterns (the orchestrator never commits to the user's
        checkout; that's the user's job).

    Used by ``runtime/mcp_config.py._sweave_mcp_entry`` to merge the
    orchestrator's profile into the per-project opencode.json, and
    by the future specialist-config renderer (R4 agent workbench).
    """
    profile: dict[str, Any] = {
        # Native opencode subagent spawning is denied on every
        # sweave-managed session. The defer path (MCP) is the only
        # way to spawn work; the DelegationManager enforces depth /
        # loop / budget on it.
        "task": "deny",
        "bash": {
            "*": list(ORCHESTRATOR_BASH_DENY) if is_orchestrator else [],
        },
    }
    return profile