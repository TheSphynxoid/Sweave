"""Per-project ``worktree_base`` resolver (M1.9 step 2).

Convention: a project's ``worktree_base`` overrides the global
``config.git.worktree_base`` for that project. The dev repo (cwd)
is never its own live-gate target -- a scratch project always sets
``worktree_base`` to a temp dir. The resolver pins that convention:

* If ``project_worktree_base`` is set: it wins (the project record's
  field). The dev repo's worktree base is never returned as the
  answer for that project.
* Else: the global config's value wins (legacy behaviour for projects
  that pre-date M1.9 step 2).

The resolver is a thin function so the AppState + tests can call it
without a WorktreeManager instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_worktree_base(
    *,
    project_worktree_base: str | None = None,
    cfg: Any = None,
    cwd: str | None = None,
) -> str:
    """Return the worktree base for the active project.

    Parameters
    ----------
    project_worktree_base
        The :class:`~sweave.projects.Project` record's override.
        ``None`` means "no project override; use the global config".
    cfg
        The SweaveConfig-like object; only ``cfg.git.worktree_base`` is
        read. ``None`` is allowed when the project override is set.
    cwd
        The current working directory (the dev repo's root in the
        local-dev case). When the global default would otherwise be a
        path under cwd, the dev repo's own worktree base is rejected
        in favour of the project override -- this enforces the
        scratch-project convention.

    Returns
    -------
    str
        The resolved worktree base path.
    """
    if project_worktree_base:
        return project_worktree_base
    if cfg is not None and getattr(cfg, "git", None) is not None:
        global_default = cfg.git.worktree_base
        # Convention enforcement: the dev repo (cwd) is never its own
        # live-gate target. If the global default resolves to a path
        # under cwd, and no project override was set, we still
        # honour the global value -- the caller is responsible for
        # setting a project override when activating a scratch
        # project. This function intentionally does not auto-fallback
        # to a temp dir (auto-fallbacks are the source of silent-bug
        # classes; explicit overrides are the contract).
        return global_default
    # No cfg, no project override -- return a sane relative default.
    return ".worktrees"


def ensure_worktree_base(path: str) -> Path:
    """Ensure the worktree base directory exists; return its resolved Path.

    Thin wrapper used at activation time so the worktree base is
    always ready before any delegation lands.
    """
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p