"""Opencode-native permission profiles for Sweave's two roles.

Two roles, two agents (rendered into the per-project opencode.json
``agent`` map by ``runtime/mcp_config.py``; pinned per message by
``SpecialistRuntime``):

* **sweave-orchestrator** -- the user-facing conversational
  supervisor. Denies native subagent spawning (``task``) and
  git-mutation bash on the user's checkout. Keeps the sweave MCP
  tools (defer / list_specialists / ask_human): the orchestrator is
  their only consumer.
* **sweave-specialist** -- implementation workers in disposable
  worktrees. Denies ``task`` (no native sub-subagents outside the
  DelegationManager) and ``sweave_*`` (no defer / list_specialists /
  ask_human: specialists do the work themselves; opencode matches
  permission keys as wildcards against tool names, so ``sweave_*``
  covers every tool of the ``sweave`` MCP server). No git deny:
  specialists commit freely in their branches per the
  commit-authority map (2026-09-04).

Shape note: this is the opencode per-agent ``permission`` block
(``{tool: action | {pattern: action}}``). An earlier revision
emitted ``{"bash": {"*": [...]}}`` (a list value) nested inside the
``mcp.sweave`` server entry -- doubly dead: the schema only allows
action/object values, and unknown keys inside an MCP server entry
are stripped (``additionalProperties: false``), so the M1.9 profile
never took effect anywhere (confirmed against the live
``GET /config`` 2026-09-09). Profiles now render here and are
consumed as ``agent.<name>.permission``.
"""

from __future__ import annotations

from typing import Any


# The orchestrator's git-mutation deny list. Globs are matched by
# opencode's permission system against the parsed command; the list
# below covers every git mutation the orchestrator must NOT perform
# on the user's checkout.
ORCHESTRATOR_BASH_DENY: tuple[str, ...] = (
    "git commit*",
    "git merge*",
    "git push*",
    "gh pr merge*",
    "git rebase*",
    "git reset --hard*",
)

# MCP tool-name prefix for the sweave server. Opencode exposes MCP
# tools as ``{server}_{tool}`` (verified: ``sweave_list_specialists``
# in a live session's parts), so one wildcard gates the whole
# sweave surface for sessions that must not see it.
SWEAVE_MCP_TOOL_PATTERN = "sweave_*"


def render_agent_permission_profile(*, is_orchestrator: bool) -> dict[str, Any]:
    """Render the opencode per-agent ``permission`` block.

    Orchestrator:
        ``{"task": "deny", "bash": {"git commit*": "deny", ...}}`` --
        no native subagents, no git mutation on the user's checkout.
        File reads/writes stay at the default (allow): the
        orchestrator keeps file access by user ruling 2026-09-09.

    Specialist:
        ``{"task": "deny", "sweave_*": "deny"}`` -- no native
        subagents, no sweave MCP tools. Git (incl. commit) stays
        allowed: specialists commit in their disposable branches.
    """
    profile: dict[str, Any] = {
        # Native opencode subagent spawning is denied on every
        # sweave-managed session. The defer path (MCP) is the only
        # way to spawn work; the DelegationManager enforces depth /
        # loop / budget on it.
        "task": "deny",
    }
    if is_orchestrator:
        profile["bash"] = {pat: "deny" for pat in ORCHESTRATOR_BASH_DENY}
    else:
        profile[SWEAVE_MCP_TOOL_PATTERN] = "deny"
    return profile
