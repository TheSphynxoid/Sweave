"""Fix-round lineage: fix children start from the reviewed branch.

Without lineage every delegation mints a fresh tree off HEAD, so a
fix child starts blind to the code under review (the review branch
is unmerged by definition while its record sits in ``review``).
With it, a ``fix_of`` child cuts its tree from the reviewed
record's branch; anything unverifiable falls back to HEAD with a
trace event — lineage never fails a turn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from tests.conftest import fake_worktree_manager_factory


class _StubRuntime:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


def _runner(tmp_path: Path, run_calls: list, factory=None, **kw):
    kw.setdefault("delegate_tool", None)
    kw.setdefault("delegation_stores", PerProjectDelegationStores())
    kw.setdefault("project_dir_resolver", lambda name: tmp_path)
    kw.setdefault("traces_dir", tmp_path / "traces")
    kw.setdefault("specialist_runtime", _StubRuntime(run_calls))
    kw.setdefault("specialist_factory", lambda agent, project=None: None)
    kw.setdefault("turn_timeout", 30.0)
    if factory is None:
        factory, _ = fake_worktree_manager_factory(tmp_path / "wt-root")
    kw.setdefault("worktree_manager_factory", factory)
    return JobRunner(**kw)  # type: ignore[arg-type]


async def _reviewed_record(runner, store, branch: str | None):
    """A settled-in-review delegation carrying *branch*."""
    from sweave.runtime.delegation_store import Delegation

    d = await runner.submit(agent="backend", task="build it", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    await store.update(terminal.delegation_id, branch=branch)
    rec = store.get(terminal.delegation_id)
    assert rec is not None
    return rec


@pytest.mark.asyncio
async def test_fix_child_cut_from_reviewed_branch(tmp_path: Path):
    run_calls: list = []
    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    runner = _runner(tmp_path, run_calls, factory=factory)
    store = await runner.stores.for_project(tmp_path)
    reviewed = await _reviewed_record(runner, store, "sweave/old-impl/backend")

    fix = await runner.submit(
        agent="backend", task="fix it", project_name="p1",
        parent_task_id=reviewed.delegation_id,
        fix_of=reviewed.delegation_id, fix_round=1,
    )
    terminal = await runner.wait(fix.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    assert calls["bases"][-1] == (fix.task_id, "backend", "sweave/old-impl/backend")
    # The fix record still names its OWN branch (lineage is the base,
    # not an identity change).
    assert terminal.branch == f"sweave/{fix.task_id}/backend"


@pytest.mark.asyncio
async def test_non_fix_delegation_starts_at_head(tmp_path: Path):
    run_calls: list = []
    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    runner = _runner(tmp_path, run_calls, factory=factory)
    d = await runner.submit(agent="backend", task="fresh work", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    assert calls["bases"][-1] == (d.task_id, "backend", None)


@pytest.mark.asyncio
async def test_gone_branch_falls_back_with_trace(tmp_path: Path):
    from sweave.runtime.trace_log import read_trace

    run_calls: list = []
    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    calls["branches_gone"].append("sweave/gone/backend")
    runner = _runner(tmp_path, run_calls, factory=factory)
    store = await runner.stores.for_project(tmp_path)
    reviewed = await _reviewed_record(runner, store, "sweave/gone/backend")

    fix = await runner.submit(
        agent="backend", task="fix it", project_name="p1",
        parent_task_id=reviewed.delegation_id,
        fix_of=reviewed.delegation_id, fix_round=1,
    )
    terminal = await runner.wait(fix.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    assert calls["bases"][-1] == (fix.task_id, "backend", None)
    events = read_trace(fix.delegation_id, base_dir=tmp_path / "traces")
    kinds = [e.get("event") for e in events]
    assert "worktree_base_fallback" in kinds


@pytest.mark.asyncio
async def test_fix_without_branch_falls_back(tmp_path: Path):
    run_calls: list = []
    factory, calls = fake_worktree_manager_factory(tmp_path / "wt-root")
    runner = _runner(tmp_path, run_calls, factory=factory)
    store = await runner.stores.for_project(tmp_path)
    reviewed = await _reviewed_record(runner, store, None)

    fix = await runner.submit(
        agent="backend", task="fix it", project_name="p1",
        parent_task_id=reviewed.delegation_id,
        fix_of=reviewed.delegation_id, fix_round=1,
    )
    terminal = await runner.wait(fix.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    assert calls["bases"][-1] == (fix.task_id, "backend", None)


def _git(args: list[str], cwd: Path) -> str:
    from sweave.platform import run_no_window

    return run_no_window(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.asyncio
async def test_real_git_fix_tree_contains_reviewed_commit(tmp_path: Path):
    """End-to-end on a real repo: the fix tree starts ON the reviewed
    branch — the fixer sees the code under review."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "f.txt").write_text("base", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "base"], repo)

    run_calls: list = []
    runner = _runner(tmp_path, run_calls, worktree_manager_factory=None)
    runner.project_dir_resolver = lambda name: repo  # type: ignore[assignment]
    store = await runner.stores.for_project(repo)

    reviewed = await runner.submit(
        agent="backend", task="build it", project_name="p1"
    )
    terminal = await runner.wait(reviewed.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    # The implementation commit lives only on the review branch.
    impl_tree = Path(terminal.worktree_path or "")
    (impl_tree / "impl.txt").write_text("the implementation", encoding="utf-8")
    _git(["add", "."], impl_tree)
    _git(["commit", "-m", "implement the thing"], impl_tree)

    fix = await runner.submit(
        agent="backend", task="fix it", project_name="p1",
        parent_task_id=reviewed.delegation_id,
        fix_of=reviewed.delegation_id, fix_round=1,
    )
    fix_terminal = await runner.wait(fix.delegation_id, timeout=10)
    assert fix_terminal is not None and fix_terminal.status == "review"
    fix_tree = Path(fix_terminal.worktree_path or "")
    assert fix_tree.exists()
    # Continuity: the reviewed commit is IN the fix tree.
    assert (fix_tree / "impl.txt").read_text(encoding="utf-8") == "the implementation"
    log = _git(["log", "--oneline", fix_terminal.branch or ""], repo)
    assert "implement the thing" in log
    # And the fix branch descends from the review branch.
    _git(["merge-base", "--is-ancestor", terminal.branch or "",
          fix_terminal.branch or ""], repo)


def test_fix_brief_carries_lineage_pointer():
    from sweave.web.routers.delegations import _build_fix_task
    from sweave.runtime.delegation_store import Delegation

    rec = Delegation(agent="backend", task="build it", project_name="demo")
    rec.task_id = "abc123"
    rec.branch = "sweave/abc123/backend"
    rec.worktree_path = "/wt/abc123-backend"
    task = _build_fix_task(rec, comments="fix the null check",
                           reviewer="reviewer", fix_round=1)
    assert "[fix-round 1" in task
    assert "sweave/abc123/backend" in task
    assert "/wt/abc123-backend" in task
