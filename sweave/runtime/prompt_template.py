"""Per-delegation system-prompt template variables (``{{var}}``).

A specialist's ``system_prompt`` may reference live values that are
only known at delegation time — the task worktree, the git state,
today's date — in the same spirit as Claude Code-style agent apps
(``${WORKING_DIRECTORY}`` / ``${GIT_STATUS}`` / ...). Sweave spells
them ``{{var}}`` (the existing ``{{...}}`` convention from the
rule-router's model templates); ``${VAR}`` is intentionally NOT
expanded so shell snippets and code samples in prompts pass through
untouched.

Vocabulary (all values are strings; missing/unknown renders ``""``
for known-absent and is left intact for unknown names):

* ``task`` — the delegation's task text.
* ``delegation_id`` / ``task_id`` — the Delegation record ids.
* ``specialist`` — the specialist name (``agent`` is an alias).
* ``project_name`` — the Sweave project ("" when unscoped).
* ``worktree_path`` — the task worktree (absolute).
* ``worktree_policy`` — the specialist's isolation intent
  (``isolated`` / ``inherit`` / ``none``, "" when unknown).
* ``workspace`` — one pre-built sentence stating where this turn
  runs and what may be done there (owns vs shares vs live root).
  Derived from ``(kind, worktree_policy, worktree_owned,
  parent_task_id, branch)``; see :func:`build_workspace_line`.
  Prompts should prefer ``{{workspace}}`` over describing the
  worktree by hand — the sentence already reflects the resolved
  policy (an ``inherit`` run shared from a treeless chat parent
  names the orchestrator's view BY DESIGN, never as an accident).
* ``model`` — qualified ``provider/model`` for this turn ("" when
  unresolved).
* ``today`` — local date ``YYYY-MM-DD``.
* ``branch`` / ``git_status`` / ``recent_commits`` — best-effort git
  reads of the worktree (windowless, 10s cap); "" outside a repo or
  on any failure. ``git_status`` is ``--porcelain`` truncated to
  2000 chars; ``recent_commits`` is ``log --oneline -10``.
* ``session_id`` — the durable opencode session id when known.

Turn semantics: prompts WITHOUT variables keep the legacy behaviour
(one-off send on session create). Prompts WITH variables are
rendered fresh and sent as a system message on EVERY delegation —
per-turn values (worktree, git state, date) would otherwise bake
the first turn's values into a reused session. The template check
is syntactic (`has_template_vars`), so static prompts pay nothing.

Unknown ``{{names}}`` are left verbatim (never a hard error): a
prompt shared from another agent app degrades to literal text
instead of failing the turn.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: Names the renderer knows. Anything else inside ``{{ }}`` passes
#: through untouched.
KNOWN_VARIABLES: tuple[str, ...] = (
    "task",
    "delegation_id",
    "task_id",
    "specialist",
    "agent",
    "project_name",
    "worktree_path",
    "worktree_policy",
    "workspace",
    "model",
    "today",
    "branch",
    "git_status",
    "recent_commits",
    "session_id",
)


def template_var_names(text: str) -> list[str]:
    """Return the ``{{var}}`` names referenced in *text*, in order."""
    return _TEMPLATE_RE.findall(text or "")


def has_template_vars(text: str) -> bool:
    """True when *text* references at least one ``{{var}}``.

    ``${VAR}`` (other agent apps' spelling) deliberately does NOT
    count — those pass through literally.
    """
    return bool(_TEMPLATE_RE.search(text or ""))


def render_prompt_template(text: str, context: Mapping[str, Any]) -> str:
    """Substitute ``{{var}}`` from *context*; unknown names stay verbatim."""

    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in context:
            return match.group(0)
        value = context[name]
        return "" if value is None else str(value)

    return _TEMPLATE_RE.sub(_replace, text or "")


def _git_output(args: list[str], worktree: Path) -> str:
    """Best-effort git read; "" on any failure (non-repo, no git, timeout)."""
    from sweave.platform import check_output_no_window

    try:
        out = check_output_no_window(
            ["git", *args],
            cwd=str(worktree),
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return out.decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001 — git state is advisory, never fatal
        return ""


def build_workspace_line(
    *,
    kind: str = "task",
    policy: str | None = None,
    owned: bool = True,
    parent_task_id: str | None = None,
    worktree_path: str = "",
    branch: str = "",
) -> str:
    """One sentence stating where this turn runs and the ground rules.

    The sentence is the concluded policy fact — prompts embed
    ``{{workspace}}`` instead of branching on the policy themselves
    (the renderer is dumb substitution, no conditionals).

    * chat turns never take a tree: project root, live tree.
    * owned trees (``isolated``): the run's own tree, commit freely.
    * shared trees (``inherit`` resolving to a ``sweave/`` branch):
      coordinate with the owner, never retire/remove.
    * ``inherit`` landing on project root (treeless parent, e.g. a
      chat delegation): the orchestrator's view BY DESIGN (user
      ruling 2026-09-15 — minting the orchestrator its own tree
      would be waste), stated as intentional, never as an accident.
    * ``none``: project root by policy.
    """
    policy = (policy or "").strip() or "isolated"
    if kind == "chat":
        return (
            f"You work directly in the project root {worktree_path} "
            "(no worktree — chat turns never take one). "
            "This is the live tree; be careful."
        )
    if owned:
        return (
            f"Your isolated git worktree is {worktree_path} "
            f"(branch {branch or 'unknown'}). It is yours alone: do all "
            "file work here and commit freely. Never `cd` elsewhere "
            "unless asked."
        )
    if policy == "inherit" and (branch or "").startswith("sweave/"):
        return (
            f"You share the worktree {worktree_path} (branch {branch}) "
            f"inherited from {parent_task_id or 'its owner'}. It is NOT "
            "yours to retire: coordinate commits with its owner and "
            "never remove it."
        )
    if policy == "inherit":
        return (
            f"You work directly in the project root {worktree_path} "
            f"(branch {branch or 'unknown'}) — `inherit` was requested "
            f"but the parent ({parent_task_id or 'none'}) has no tree, "
            "so you see the orchestrator's view by design. "
            "This is the live tree; be careful."
        )
    return (
        f"You work directly in the project root {worktree_path} "
        f"(branch {branch or 'unknown'}) — policy `none` takes no "
        "worktree. This is the live tree; be careful."
    )


def build_template_context(
    *,
    specialist: Any,
    delegation: Any,
    worktree_path: Path,
    model: str | None = None,
) -> dict[str, str]:
    """Build the render context for one delegation."""
    worktree = Path(worktree_path)
    project_name = getattr(delegation, "project_name", None) or ""
    branch = _git_output(["rev-parse", "--abbrev-ref", "HEAD"], worktree)
    status = _git_output(["status", "--porcelain"], worktree)[:2000]
    log = _git_output(["log", "--oneline", "-10"], worktree)
    name = getattr(specialist, "name", "") or ""
    policy = getattr(specialist, "worktree_policy", None) or ""
    kind = getattr(delegation, "kind", None) or "task"
    owned = getattr(delegation, "worktree_owned", True)
    if owned is None:
        owned = True
    parent_task_id = getattr(delegation, "parent_task_id", None) or None
    return {
        "task": getattr(delegation, "task", "") or "",
        "delegation_id": getattr(delegation, "delegation_id", "") or "",
        "task_id": getattr(delegation, "task_id", "") or "",
        "specialist": name,
        "agent": name,
        "project_name": project_name,
        "worktree_path": str(worktree),
        "worktree_policy": policy,
        "workspace": build_workspace_line(
            kind=kind,
            policy=policy,
            owned=bool(owned),
            parent_task_id=parent_task_id,
            worktree_path=str(worktree),
            branch=branch,
        ),
        "model": model or "",
        "today": date.today().isoformat(),
        "branch": branch,
        "git_status": status,
        "recent_commits": log,
        "session_id": getattr(specialist, "session_id", None) or "",
    }
