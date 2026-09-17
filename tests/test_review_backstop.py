"""Review backstop: uncovered review-requests get reviewer coverage.

Incident 2026-09-17 (session Sweave-20260916-215042-3dba3a): the
orchestrator verbalized reviewer need in synthesis ("both need
reviewer pass") and the turn closed with zero reviewer
delegations — intent with no execution. When a turn ends, every
direct child still in ``review`` with a review_request but no
reviewer coverage gets a reviewer delegation parented to ITSELF
(the review target, per the synthesis contract), validated
through the same chain rules as an MCP defer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
)


def _project_manager(tmp_path: Path):
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "sweave-home")
    pm.create_project("demo", path=tmp_path)
    return pm


class _StubRunner:
    """JobRunner double: validates nothing itself, records submits."""

    def __init__(self, store, manager=None) -> None:
        self._store = store
        self.delegation_manager = manager
        self.submits: list[dict[str, Any]] = []

    async def submit(self, **kwargs):
        self.submits.append(kwargs)
        d = Delegation(
            agent=kwargs["agent"],
            task=kwargs["task"],
            parent_task_id=kwargs.get("parent_task_id"),
            project_name=kwargs.get("project_name"),
            status="queued",
        )
        await self._store.add(d)
        return d

    async def cancel_subtree(self, *args, **kwargs):
        return []


def _stub_runtime():
    class _StubRuntime:
        async def run(self, **kwargs):
            await asyncio.Event().wait()
            return "never"

    return _StubRuntime()


def _chat_loop(pm, stores, runner, project_dir: Path, **kw):
    from sweave.chat.loop import ChatLoop

    return ChatLoop(
        project_manager=pm,
        specialist_runtime=_stub_runtime(),
        specialist_factory=lambda name, project=None: None,
        project_dir_resolver=lambda name: project_dir,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=60.0,
        job_runner=runner,
        **kw,
    )


def _manager(**kw):
    from sweave.runtime.delegation_manager import DelegationManager

    return DelegationManager(max_depth=kw.get("max_depth", 2),
                             chain_budget=kw.get("chain_budget", 200_000))


def _review_request() -> dict:
    return {
        "reviewer_hint": "reviewer",
        "diff_ref": {"branch": "sweave/x/backend", "worktree_path": "/tmp/wt",
                     "pr_url": None},
        "manifest_summary": "implement the thing",
        "confidence": 0.8,
    }


async def _seed_turn(store, pm, session_id: str = "s1"):
    session = pm.create_session("demo", session_name=session_id)
    chat = Delegation(agent="orchestrator", task="q", project_name="demo",
                      kind="chat", status="running")
    await store.add(chat)
    impl = Delegation(agent="backend", task="build it", project_name="demo",
                      parent_task_id=chat.delegation_id, status="review",
                      depth=1, chain_root_id=chat.delegation_id)
    impl.review_request = _review_request()
    await store.add(impl)
    return session, chat, impl


@pytest.mark.asyncio
async def test_backstop_spawns_reviewer_parented_to_target(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _StubRunner(store, _manager())
    chat = _chat_loop(pm, stores, runner, tmp_path)
    session, chat_rec, impl = await _seed_turn(store, pm)

    await chat._backstop_uncovered_reviews(session.id, chat_rec.delegation_id)

    assert len(runner.submits) == 1
    sub = runner.submits[0]
    assert sub["agent"] == "reviewer"
    assert sub["parent_task_id"] == impl.delegation_id
    assert sub["project_name"] == "demo"
    assert sub["manifest"]["source"] == "review_backstop"
    assert "sweave/x/backend" in sub["task"]
    # Persisted where cancel will find it (the incident's lesson).
    rec = store.get(sub["parent_task_id"])
    assert rec is not None
    kids = [r for r in store.list()
            if r.parent_task_id == impl.delegation_id]
    assert len(kids) == 1 and kids[0].agent == "reviewer"


@pytest.mark.asyncio
async def test_backstop_skips_covered_targets(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _StubRunner(store, _manager())
    chat = _chat_loop(pm, stores, runner, tmp_path)
    session, chat_rec, impl = await _seed_turn(store, pm)
    # A live reviewer child already covers the target.
    cover = Delegation(agent="reviewer-specialist", task="review it",
                       project_name="demo", parent_task_id=impl.delegation_id,
                       status="review", depth=2,
                       chain_root_id=chat_rec.delegation_id)
    await store.add(cover)

    await chat._backstop_uncovered_reviews(session.id, chat_rec.delegation_id)

    assert runner.submits == []


@pytest.mark.asyncio
async def test_backstop_skips_judged_targets(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _StubRunner(store, _manager())
    chat = _chat_loop(pm, stores, runner, tmp_path)
    session, chat_rec, impl = await _seed_turn(store, pm)
    impl.verdict = {"decision": "approve", "reviewer": "human"}
    await store.update(impl.delegation_id, verdict=impl.verdict)

    await chat._backstop_uncovered_reviews(session.id, chat_rec.delegation_id)

    assert runner.submits == []


@pytest.mark.asyncio
async def test_backstop_respawns_after_failed_reviewer(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _StubRunner(store, _manager())
    chat = _chat_loop(pm, stores, runner, tmp_path)
    session, chat_rec, impl = await _seed_turn(store, pm)
    dead = Delegation(agent="reviewer", task="review it",
                      project_name="demo", parent_task_id=impl.delegation_id,
                      status="failed", depth=2,
                      chain_root_id=chat_rec.delegation_id)
    await store.add(dead)

    await chat._backstop_uncovered_reviews(session.id, chat_rec.delegation_id)

    assert len(runner.submits) == 1


@pytest.mark.asyncio
async def test_backstop_respects_chain_rules(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    # max_depth=0: any reviewer child exceeds it.
    runner = _StubRunner(store, _manager(max_depth=0))
    chat = _chat_loop(pm, stores, runner, tmp_path)
    session, chat_rec, impl = await _seed_turn(store, pm)

    await chat._backstop_uncovered_reviews(session.id, chat_rec.delegation_id)

    assert runner.submits == []


@pytest.mark.asyncio
async def test_backstop_ignores_non_review_children(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _StubRunner(store, _manager())
    chat = _chat_loop(pm, stores, runner, tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat_rec = Delegation(agent="orchestrator", task="q",
                          project_name="demo", kind="chat", status="running")
    await store.add(chat_rec)
    # Done child with a stale request, failed child, request-less child.
    for status, req in (("done", _review_request()), ("failed", _review_request()),
                        ("review", None)):
        d = Delegation(agent="backend", task="t", project_name="demo",
                       parent_task_id=chat_rec.delegation_id, status=status,
                       depth=1, chain_root_id=chat_rec.delegation_id)
        if req is not None:
            d.review_request = req
        await store.add(d)

    await chat._backstop_uncovered_reviews(session.id, chat_rec.delegation_id)

    assert runner.submits == []


@pytest.mark.asyncio
async def test_backstop_without_runner_is_silent(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    chat = _chat_loop(pm, stores, None, tmp_path)
    session = pm.create_session("demo", session_name="s1")
    # Must never raise (the turn already finalized).
    await chat._backstop_uncovered_reviews(session.id, "chat-missing")


class _DeferringRuntime:
    """Stub runtime simulating an orchestrator turn that defers work.

    Seeds a review-status child (with review_request) parented to
    the turn's own delegation, then returns text — the synthesis
    path a real dispatch-then-synthesize turn takes.
    """

    def __init__(self, store) -> None:
        self._store = store

    async def run(self, **kwargs):
        delegation = kwargs["delegation"]
        impl = Delegation(
            agent="backend", task="build it", project_name="demo",
            parent_task_id=delegation.delegation_id, status="review",
            depth=1, chain_root_id=delegation.delegation_id,
        )
        impl.review_request = _review_request()
        await self._store.add(impl)
        return "done, two slices in review"


@pytest.mark.asyncio
async def test_completed_turn_triggers_backstop_end_to_end(tmp_path: Path):
    """The owner-runner wiring: a normally completing turn runs the
    backstop before returning, so reviewer coverage exists without
    any model follow-up."""
    from sweave.chat.loop import ChatLoop

    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _StubRunner(store, _manager())
    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=_DeferringRuntime(store),
        specialist_factory=lambda name, project=None: None,
        project_dir_resolver=lambda name: tmp_path,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=30.0,
        job_runner=runner,
    )
    session = pm.create_session("demo", session_name="s-e2e")
    result = await asyncio.wait_for(
        chat.run_turn(session_id=session.id, user_content="go?"),
        timeout=60,
    )
    assert "two slices" in result["content"]
    assert len(runner.submits) == 1
    sub = runner.submits[0]
    assert sub["agent"] == "reviewer"
    impl = next(r for r in store.list()
                if r.agent == "backend" and r.status == "review")
    assert sub["parent_task_id"] == impl.delegation_id
