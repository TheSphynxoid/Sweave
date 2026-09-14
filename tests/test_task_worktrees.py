"""Task worktree isolation tests (DESIGN principles #2 + #3 restored).

Implementation delegations run in an isolated git worktree +
``sweave/{task}/{agent}`` branch — never in the live project tree.
Chat turns (orchestrator) stay in the project dir.

* creation sets the record fields (worktree_path, branch) and the
  runtime receives the tree while the project scope stays put;
* creation failure fails loud (no silent in-tree fallback);
* ``done``/``failed`` retires the tree (branch kept); ``review``
  keeps it; chat owns no tree;
* override bases are honored; the task tree joins the allowed roots;
* one REAL-git proof: branch created on disk, tree removed at
  settle, branch retained.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from tests.conftest import fake_worktree_manager_factory


def _wt_factory(root=None):
    return fake_worktree_manager_factory(root)[0]


def _wt_calls_factory(root=None):
    return fake_worktree_manager_factory(root)


class _StubRuntime:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


def _runner(tmp_path: Path, run_calls: list, **kw):
    kw.setdefault("delegate_tool", None)
    kw.setdefault("delegation_stores", PerProjectDelegationStores())
    kw.setdefault("project_dir_resolver", lambda name: tmp_path)
    kw.setdefault("traces_dir", tmp_path / "traces")
    kw.setdefault("specialist_runtime", _StubRuntime(run_calls))
    kw.setdefault("specialist_factory", lambda agent, project=None: None)
    kw.setdefault("turn_timeout", 30.0)
    kw.setdefault("worktree_manager_factory", _wt_factory(tmp_path / "wt-root"))
    return JobRunner(**kw)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_creation_sets_record_and_scopes_runtime(tmp_path: Path):
    run_calls: list = []
    runner = _runner(tmp_path, run_calls)
    d = await runner.submit(agent="backend", task="t", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    # Record carries the isolation pointer.
    assert terminal.worktree_path and terminal.worktree_path.endswith(
        f"{d.task_id}-backend"
    )
    assert terminal.branch == f"sweave/{d.task_id}/backend"
    # The runtime ran IN the tree while the project scope stayed put.
    assert run_calls and Path(run_calls[0]["worktree_path"]) == Path(
        terminal.worktree_path
    )
    assert run_calls[0]["project_dir"] == tmp_path
    # The task's own tree joins the allowed roots.
    assert str(terminal.worktree_path) in (run_calls[0]["permission_roots"] or [])


@pytest.mark.asyncio
async def test_review_keeps_tree_done_retires_it(tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    factory, calls = _wt_calls_factory(tmp_path / "wt-root")
    run_calls: list = []
    runner = _runner(tmp_path, run_calls, worktree_manager_factory=factory)
    d = await runner.submit(agent="backend", task="t", project_name="p1")
    await runner.wait(d.delegation_id, timeout=10)
    assert len(calls["created"]) == 1
    assert calls["removed"] == []
    # review (the wait above) keeps the tree for human inspection.
    store = await runner.stores.for_project(tmp_path)
    rec = store.get(d.delegation_id)
    assert rec is not None and rec.status == "review"
    # Settle to done: the tree retires, tested via the same choke
    # point the cancel path uses.
    await runner._transition(
        rec, store, TraceLog(d.delegation_id, base_dir=tmp_path), "done"
    )
    assert calls["removed"] == [(rec.task_id, "backend")]


@pytest.mark.asyncio
async def test_failed_settle_retires_tree(tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    factory, calls = _wt_calls_factory(tmp_path / "wt-root")

    class _BoomRuntime:
        async def run(self, **kwargs):
            raise RuntimeError("boom")

    runner = JobRunner(
        delegate_tool=None,  # type: ignore[arg-type]
        delegation_stores=PerProjectDelegationStores(),
        project_dir_resolver=lambda name: tmp_path,
        traces_dir=tmp_path / "traces",
        specialist_runtime=_BoomRuntime(),  # type: ignore[arg-type]
        specialist_factory=lambda agent, project=None: None,
        turn_timeout=30.0,
        worktree_manager_factory=factory,
    )
    d = await runner.submit(agent="backend", task="t", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "failed"
    assert calls["removed"] == [(terminal.task_id, "backend")]


@pytest.mark.asyncio
async def test_creation_failure_fails_loud_not_in_tree(tmp_path: Path):
    """The real manager against a non-git dir: the delegation fails
    with a worktree error — never silently runs in the live tree."""
    run_calls: list = []
    runner = _runner(tmp_path, run_calls, worktree_manager_factory=None)
    # NOTE: _runner defaults the fake factory; None opts back into
    # the real WorktreeManager (git CLI).
    d = await runner.submit(agent="backend", task="t", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "failed"
    assert terminal.error and terminal.error.startswith("[worktree error:")
    assert run_calls == []


@pytest.mark.asyncio
async def test_override_base_honored(tmp_path: Path):
    run_calls: list = []
    override = tmp_path / "custom-base"
    runner = _runner(
        tmp_path,
        run_calls,
        worktree_base_resolver=lambda name: str(override),
    )
    d = await runner.submit(agent="backend", task="t", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    assert Path(terminal.worktree_path or "").parent == override.resolve()


def _git(args: list[str], cwd: Path) -> None:
    from sweave.platform import run_no_window

    run_no_window(["git", *args], cwd=str(cwd), capture_output=True, check=True)


@pytest.mark.asyncio
async def test_real_git_branch_created_tree_removed_branch_kept(tmp_path: Path):
    """End-to-end on a real repo: branch + tree appear at run start;
    settle removes the tree and keeps the branch (PR-able)."""
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
    d = await runner.submit(agent="backend", task="t", project_name="p1")
    terminal = await runner.wait(d.delegation_id, timeout=10)
    assert terminal is not None and terminal.status == "review"
    tree = Path(terminal.worktree_path or "")
    assert tree.exists()
    assert terminal.branch == f"sweave/{d.task_id}/backend"

    from sweave.runtime.trace_log import TraceLog

    store = await runner.stores.for_project(repo)
    rec = store.get(d.delegation_id)
    assert rec is not None
    await runner._transition(
        rec, store, TraceLog(d.delegation_id, base_dir=tmp_path), "done"
    )
    assert not tree.exists()
    # Branch retained for forensics / future PR flow.
    _git(["rev-parse", "--verify", f"refs/heads/{terminal.branch}"], repo)
