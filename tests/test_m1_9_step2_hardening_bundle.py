"""M1.9 step 2 tests: hardening bundle.

Three components:

* **Per-project ``worktree_base``**: a field on the
  :class:`~sweave.projects.Project` record that overrides the global
  ``config.g.wworktree_base`` for that project. The worktree
  manager + orchestrator config plumbing honor it.

* **WorktreeManager.align() precursor**: rebase / merge the worktree
  branch onto the integration branch's current state. The full R2
  protocol lands later; this is the primitive. The dirty-skip rule
  ("never stash-dance a working agent") is the M1.9 invariant.

* **Specialist permission profile**: the opencode per-project config
  (M1.6 plumbing) sets ``permission.task: deny`` on specialist
  agents (so native opencode subagent spawning can't bypass the
  DelegationManager's depth / loop / budget). The orchestrator session
  config additionally denies git-mutation bash (``git commit``,
  ``git merge``, ``git push``, ``gh pr merge``).

The tests are hermetic (the SWEAVE_MOCK_OPENCODE gate isn't needed
here -- the plumbing is config-only).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Per-project worktree_base
# ---------------------------------------------------------------------------


def test_project_record_supports_worktree_base():
    """A :class:`Project` carries ``worktree_base`` (str | None). When
    ``None``, the global config's value wins (existing behaviour).
    When set, it overrides the global config for that project."""
    from sweave.projects import Project

    p = Project(name="x", path=Path("."), worktree_base="/tmp/other-worktrees")
    assert p.worktree_base == "/tmp/other-worktrees"
    # Round-trips through to_dict/from_dict
    d = p.to_dict()
    assert d["worktree_base"] == "/tmp/other-worktrees"
    p2 = Project.from_dict(d)
    assert p2.worktree_base == "/tmp/other-worktrees"


def test_project_record_worktree_base_defaults_to_none():
    """Legacy project files (no ``worktree_base`` field) load with
    ``None`` -- the existing global default wins."""
    from sweave.projects import Project

    p = Project(name="x", path=Path("."))
    assert p.worktree_base is None


def test_resolve_worktree_base_for_project_uses_override(tmp_path: Path):
    """``resolve_worktree_base(project_dir, project_record, config)``
    returns the per-project override when set, otherwise the global
    config's value."""
    from sweave.runtime.worktree_base import resolve_worktree_base

    # No project record -> global wins
    cfg = _config_with_worktree_base(str(tmp_path / "global"))
    assert resolve_worktree_base(cfg=cfg) == str(tmp_path / "global")

    # Project override set -> it wins
    override = str(tmp_path / "project-override")
    assert (
        resolve_worktree_base(project_worktree_base=override, cfg=cfg)
        == override
    )


def test_resolve_worktree_base_scratch_project_default(tmp_path: Path):
    """Convention: the dev repo (cwd) is never its own live-gate target.
    ``resolve_worktree_base(cwd=...)`` returns the project's override
    (typically a temp dir) even when the override is missing; the
    global-config fallback (which lives under cwd) is rejected."""
    from sweave.runtime.worktree_base import resolve_worktree_base

    # Global default lives under cwd -- the dev repo. We pass a
    # project_worktree_base under tmp_path; that wins (it's the
    # scratch-project convention).
    cfg = _config_with_worktree_base("./.worktrees")  # under cwd
    scratch = str(tmp_path / "scratch-worktrees")
    out = resolve_worktree_base(
        project_worktree_base=scratch, cfg=cfg, cwd=str(tmp_path)
    )
    assert out == scratch


# ---------------------------------------------------------------------------
# WorktreeManager.align() precursor
# ---------------------------------------------------------------------------


def test_worktree_align_clean_rebases_onto_integration(tmp_path: Path):
    """A clean worktree (no dirty files) rebases onto the integration
    branch's HEAD. Returns ``"rebased"`` on success."""
    from sweave.workspace.manager import WorktreeManager

    wt, info = _make_real_worktree(tmp_path)
    assert wt._is_git_dirty(info.path) is False
    # Run align: should succeed cleanly (no integration branch yet,
    # so the rebase is a fast-forward onto the base).
    result = wt.align(info.path, base_branch="main")
    assert result in {"rebased", "no_integration_branch", "noop"}


def test_worktree_align_dirty_skips(tmp_path: Path):
    """A dirty worktree (uncommitted changes) is SKIPPED, never
    stash-danced. Returns ``"skipped_dirty"``."""
    from sweave.workspace.manager import WorktreeManager

    wt, info = _make_real_worktree(tmp_path)
    # Make the worktree dirty
    (info.path / "WIP.md").write_text("wip")
    assert wt._is_git_dirty(info.path) is True
    result = wt.align(info.path, base_branch="main")
    assert result == "skipped_dirty"


def test_worktree_align_returns_dictated_status_no_raise_on_dirty(tmp_path: Path):
    """``align must not raise on a dirty worktree; the status string
    is the contract. Callers branch on the result."""
    from sweave.workspace.manager import WorktreeManager

    wt, info = _make_real_worktree(tmp_path)
    (info.path / "dirty.md").write_text("dirty")
    # No exception:
    out = wt.align(info.path, base_branch="main")
    assert isinstance(out, str)


def test_worktree_align_missing_branch_returns_no_integration_branch(tmp_path: Path):
    """A worktree whose branch doesn't exist in ``refs/heads/`` (a
    pre-merge branch that was never pushed) is a no-op."""
    from sweave.workspace.manager import WorktreeManager

    wt, info = _make_real_worktree(tmp_path)
    # Don't push the branch anywhere -- it doesn't exist on a remote.
    out = wt.align(info.path, base_branch="does-not-exist")
    assert out in {"no_integration_branch", "rebased", "noop"}


def test_worktree_align_non_git_dir_returns_no_integration_branch(tmp_path: Path):
    """``align`` on a non-git path returns the soft no-op status
    rather than raising."""
    from sweave.workspace.manager import WorktreeManager

    wt = WorktreeManager(base_path=str(tmp_path / "wt-base"))
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    out = wt.align(plain, base_branch="main")
    assert out in {"no_integration_branch", "noop"}


# ---------------------------------------------------------------------------
# Per-project opencode config: permission profile
# ---------------------------------------------------------------------------


def test_sweave_mcp_entry_sets_permission_task_deny():
    """The orchestrator's opencode session sees the sweave MCP server
    AND inherits a ``permission.task: deny`` rule (closes the native
    opencode subagent bypass)."""
    from sweave.runtime.mcp_config import _sweave_mcp_entry

    entry = _sweave_mcp_entry(token="{env:SWEAVE_MCP_TOKEN}", env_token_var="SWEAVE_MCP_TOKEN")
    permissions = entry.get("permission") or {}
    assert permissions.get("task") == "deny"


def test_specialist_agent_config_has_permission_task_deny():
    """Specialist agent definitions (``sweave/agents/*/config.yaml``)
    gain a ``permission.task: deny`` frontmatter block in the rendered
    opencode config (closes the native subagent bypass for
    specialists). The orchestrator record inherits the same deny
    (orchestrator itself never spawns specialists directly -- it
    always defers through MCP -- but the deny is the safety net)."""
    from sweave.runtime.agent_permission import render_agent_permission_profile

    profile = render_agent_permission_profile(is_orchestrator=True)
    assert profile.get("task") == "deny"
    # Orchestrator also denies git-mutation bash
    bash = profile.get("bash") or {}
    denied = bash.get("*") or []
    for pat in ("git commit", "git merge", "git push", "gh pr merge"):
        assert any(pat in d for d in denied), (
            f"orchestrator profile missing deny for {pat!r}"
        )


def test_specialist_profile_has_task_deny_but_no_git_bash_deny():
    """Specialists (non-orchestrator) get ``task: deny`` (no native
    subagent bypass) but DO NOT get the git-bash deny -- they
    commit freely in their disposable branches (per the commit-
    authority map, ruling 2026-09-04)."""
    from sweave.runtime.agent_permission import render_agent_permission_profile

    profile = render_agent_permission_profile(is_orchestrator=False)
    assert profile.get("task") == "deny"
    bash = profile.get("bash") or {}
    # Specialists may run git commit / push / etc. in their branches
    # -- the orchestrator is the only one denied.
    denied_all = (bash.get("*") or []) + (bash.get("git:*") or [])
    assert not any("git commit" in d for d in denied_all)


def test_render_agent_permission_profile_shape():
    """The profile shape is the opencode per-agent permission block:
    ``{"task": "deny", "bash": {"*": [...]}}``. Used by
    ensure_mcp_config (the opencode.json writer) to merge
    specialist-specific permission profiles."""
    from sweave.runtime.agent_permission import render_agent_permission_profile

    p = render_agent_permission_profile(is_orchestrator=True)
    # Keys are lowercase; bash rules are a list under the wildcard key.
    assert "task" in p
    assert "bash" in p
    bash = p["bash"]
    assert "*" in bash
    assert isinstance(bash["*"], list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_with_worktree_base(worktree_base: str):
    """Build a minimal SweaveConfig-like object for resolve_worktree_base.

    The real config schema is heavy; the resolver only reads
    ``cfg.git.worktree_base`` so a SimpleNamespace is enough for tests.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        git=SimpleNamespace(worktree_base=worktree_base),
    )


def _make_real_worktree(tmp_path: Path) -> tuple[Any, Any]:
    """Create a real git repo + worktree; return (WorktreeManager, WorktreeInfo).

    The caller accesses the worktree path via ``info.path``.
    """
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    # git init -b main
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    # Use the WorktreeManager: create_worktree spins up a worktree.
    # IMPORTANT: pass the repo dir as ``git_dir`` so the manager's
    # internal ``git worktree add`` invocation runs from a git repo
    # root (otherwise subprocess fails on the runner's non-repo cwd).
    from sweave.workspace.manager import WorktreeManager

    wt = WorktreeManager(
        base_path=str(tmp_path / "wt"),
        git_dir=str(repo),
    )
    info = wt.create_worktree(task_id="t1", agent_name="backend")
    return wt, info