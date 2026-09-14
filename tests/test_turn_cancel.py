"""User-stop (Stop button) tests: whole-subtree cancel.

Covers the 2026-09-14 incident (a wedged orchestrator turn with no
stop path — the only exit was killing the server, which lost the
turn): the running turn, its live children, and their engine
sessions are all stopped, and the partial reply persists as a
``cancelled`` bubble instead of a hole in the thread.

* idle cancel raises ``ValueError`` (the router maps it to 404);
* a running chat turn cancels end-to-end: subtree records land
  failed with the canonical cancel error, the bubble carries the
  streamed partial + ``cancelled`` metadata, the orchestrator
  binding rotates, pending escalations resolve as skipped, and the
  engine abort is attempted for live ``eng_*`` sessions;
* ``JobRunner.cancel_subtree``: unknown/terminal roots are no-ops,
  live parent+children cancel, ``review`` children are untouched;
* harness abort vocabulary: default ``False``, non-engine ids never
  touch the wire.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import (
    CANCELLED_BY_USER_ERROR,
    Delegation,
    PerProjectDelegationStores,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_manager(tmp_path: Path):
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "sweave-home")
    pm.create_project("demo", path=tmp_path)
    return pm


def _stub_runtime():
    """SpecialistRuntime double: streams one partial, then hangs."""

    class _StubRuntime:
        async def run(self, **kwargs):
            on_chunk = kwargs.get("on_chunk")
            if on_chunk is not None:
                on_chunk("partial reply so far")
                await asyncio.sleep(0.4)
            await asyncio.Event().wait()
            return "never"

    return _StubRuntime()


def _stub_job_runner(stores, project_dir: Path):
    from sweave.runtime.job_runner import JobRunner

    return JobRunner(
        delegate_tool=None,  # type: ignore[arg-type]
        delegation_stores=stores,
        project_dir_resolver=lambda name: project_dir,
        traces_dir=project_dir / "traces",
        turn_timeout=10.0,
    )


def _stub_escalation_store(calls: list):
    class _StubEscalations:
        async def skip(self, *, delegation_id: str):
            calls.append(delegation_id)
            return {"delegation_id": delegation_id, "status": "skipped"}

    return _StubEscalations()


def _chat_loop(pm, stores, runtime, project_dir: Path, **kw):
    from sweave.chat.loop import ChatLoop

    return ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=lambda name: None,
        project_dir_resolver=lambda name: project_dir,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=60.0,
        **kw,
    )


async def _wait_for_chat_delegation(store, timeout: float = 10.0):
    start = asyncio.get_running_loop().time()
    while True:
        for rec in store.list():
            if rec.kind == "chat" and rec.status == "running":
                return rec
        assert asyncio.get_running_loop().time() - start < timeout, "chat delegation never appeared"
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Idle cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_idle_raises_value_error(tmp_path: Path):
    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    chat = _chat_loop(pm, stores, _stub_runtime(), tmp_path)
    session = pm.create_session("demo", session_name="s1")
    with pytest.raises(ValueError, match="No active turn"):
        await chat.cancel_turn(session_id=session.id)


# ---------------------------------------------------------------------------
# Full running-turn cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_running_turn_stops_subtree_and_keeps_partial(tmp_path: Path, monkeypatch):
    from sweave.engine.protocol import ENGINE_HARNESS_NAME
    from sweave.harness.base import harness_registry

    pm = _project_manager(tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _stub_job_runner(stores, tmp_path)
    skips: list = []
    chat = _chat_loop(
        pm, stores, _stub_runtime(), tmp_path,
        job_runner=runner,
        escalation_store=_stub_escalation_store(skips),
    )
    session = pm.create_session("demo", session_name="s1")
    session.orchestrator_session_id = "eng_old_binding"
    pm.save_session(session)

    aborted: list = []

    class _FakeEngineHarness:
        name = ENGINE_HARNESS_NAME

        async def abort_turn(self, session_id: str) -> bool:
            aborted.append(session_id)
            return True

    monkeypatch.setitem(
        harness_registry._harnesses, ENGINE_HARNESS_NAME, _FakeEngineHarness()
    )

    turn_task = asyncio.create_task(
        chat.run_turn(session_id=session.id, user_content="is that true?")
    )
    chat_rec = await _wait_for_chat_delegation(store)
    await store.update(chat_rec.delegation_id, engine_session_id="eng_parent_1")
    # Wait for the stub's partial to reach the live snapshot (what a
    # user stopping mid-stream would see).
    start = asyncio.get_running_loop().time()
    while True:
        snap = chat.active_turn_snapshot(session.id)
        if snap is not None and snap.get("stream_text"):
            break
        assert asyncio.get_running_loop().time() - start < 10.0, "partial never streamed"
        await asyncio.sleep(0.05)
    # One live blocking child with its own engine session + driving task.
    child = Delegation(
        agent="coder", task="read the files", project_name="demo",
        parent_task_id=chat_rec.delegation_id, status="running",
        blocking=True, engine_session_id="eng_child_1",
    )
    await store.add(child)
    child_task = asyncio.create_task(asyncio.sleep(60.0))
    runner._tasks[child.delegation_id] = child_task

    bubble = await chat.cancel_turn(session_id=session.id)
    with pytest.raises(asyncio.CancelledError):
        await turn_task

    # The stopped bubble: partial kept, flagged, final.
    assert bubble["metadata"]["delegation_id"] == chat_rec.delegation_id
    assert bubble["metadata"]["cancelled"] is True
    assert bubble["metadata"]["turn_final"] is True
    assert "partial reply so far" in bubble["content"]
    assert "stopped by user" in bubble["content"]

    # Thread shows user + stopped assistant (no hole).
    session = pm.get_session(session.id)
    assert session is not None
    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant"]
    assert session.messages[1].metadata.get("cancelled") is True

    # Records: parent + child failed with the canonical error.
    parent_rec = store.get(chat_rec.delegation_id)
    assert parent_rec is not None and parent_rec.status == "failed"
    assert parent_rec.error == CANCELLED_BY_USER_ERROR
    assert "partial reply so far" in (parent_rec.output or "")
    child_rec = store.get(child.delegation_id)
    assert child_rec is not None and child_rec.status == "failed"
    assert child_rec.error == CANCELLED_BY_USER_ERROR

    # Child task actually stopped.
    assert child_task.done()

    # Binding KEPT (no-rotation invariant): the kill above owns
    # stopping the work; the next turn continues the same session.
    assert pm.get_session(session.id) is not None
    assert pm.get_session(session.id).orchestrator_session_id == "eng_old_binding"  # type: ignore[union-attr]

    # Pending questions resolved as skipped (parent + child).
    assert chat_rec.delegation_id in skips
    assert child.delegation_id in skips

    # Engine abort attempted for both live sessions.
    assert "eng_parent_1" in aborted
    assert "eng_child_1" in aborted

    # Turn registry fully released (a fresh turn may start).
    assert chat.active_turn_snapshot(session.id) is None
    assert session.id not in chat._turn_tasks


# ---------------------------------------------------------------------------
# JobRunner.cancel_subtree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_subtree_unknown_and_terminal_are_noops(tmp_path: Path):
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _stub_job_runner(stores, tmp_path)
    assert await runner.cancel_subtree("nope") == []
    done = Delegation(agent="a", task="t", project_name="demo", status="done")
    await store.add(done)
    assert await runner.cancel_subtree(done.delegation_id) == []


@pytest.mark.asyncio
async def test_cancel_subtree_stops_live_leaves_review(tmp_path: Path):
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runner = _stub_job_runner(stores, tmp_path)

    parent = Delegation(agent="orchestrator", task="q", project_name="demo", status="running")
    await store.add(parent)
    live_child = Delegation(
        agent="coder", task="work", project_name="demo",
        parent_task_id=parent.delegation_id, status="running",
    )
    await store.add(live_child)
    queued_child = Delegation(
        agent="planner", task="plan", project_name="demo",
        parent_task_id=parent.delegation_id, status="queued",
    )
    await store.add(queued_child)
    review_child = Delegation(
        agent="reviewer", task="look", project_name="demo",
        parent_task_id=parent.delegation_id, status="review",
    )
    await store.add(review_child)

    live_task = asyncio.create_task(asyncio.sleep(60.0))
    runner._tasks[live_child.delegation_id] = live_task

    cancelled = await runner.cancel_subtree(parent.delegation_id)
    assert cancelled[0] == parent.delegation_id
    assert set(cancelled) == {
        parent.delegation_id, live_child.delegation_id, queued_child.delegation_id
    }
    for did in cancelled:
        rec = store.get(did)
        assert rec is not None and rec.status == "failed"
        assert rec.error == CANCELLED_BY_USER_ERROR
    # Review is promotion state, not in-flight work: untouched.
    review_rec = store.get(review_child.delegation_id)
    assert review_rec is not None and review_rec.status == "review"
    assert live_task.done()


# ---------------------------------------------------------------------------
# Harness abort vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_harness_abort_default_is_false():
    from sweave.harness.base import AgentProcess, AgentSpec, Harness  # noqa: F401

    class _Bare(Harness):
        name = "bare"

        async def spawn(self, spec):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def attach(self, session_id, spec):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def get_default_tools(self):  # type: ignore[no-untyped-def]
            return []

        async def health_check(self) -> bool:
            return True

    assert await _Bare().abort_turn("eng_whatever") is False


@pytest.mark.asyncio
async def test_abort_engine_session_ignores_non_engine_ids():
    from sweave.harness.engine import abort_engine_session

    assert await abort_engine_session(None) is False
    assert await abort_engine_session("") is False
    assert await abort_engine_session("ses_abc123") is False


@pytest.mark.asyncio
async def test_abort_engine_session_routes_to_harness(monkeypatch):
    from sweave.engine.protocol import ENGINE_HARNESS_NAME
    from sweave.harness.base import harness_registry
    from sweave.harness.engine import abort_engine_session

    seen: list = []

    class _FakeEngineHarness:
        name = ENGINE_HARNESS_NAME

        async def abort_turn(self, session_id: str) -> bool:
            seen.append(session_id)
            return True

    monkeypatch.setitem(
        harness_registry._harnesses, ENGINE_HARNESS_NAME, _FakeEngineHarness()
    )
    assert await abort_engine_session("eng_live_1") is True
    assert seen == ["eng_live_1"]


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_api_cancel_idle_turn_is_404(monkeypatch, tmp_path):
    """POST .../turn/cancel with no live turn answers 404 (lifespan app)."""
    from pathlib import Path as PathCls

    from fastapi.testclient import TestClient

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))
    from sweave.web import state as state_mod

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):  # type: ignore[no-untyped-def]
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        from sweave.tools import DelegationResult

        class _StubDelegateTool:
            async def execute(self, agent, task, model=None, task_id=None):
                return DelegationResult(
                    success=False, agent=agent, task_id=task_id or "stub",
                    output="", error="stubbed in test",
                )

        state.delegate_tool = _StubDelegateTool()
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)
    from sweave.web.server import app

    with TestClient(app) as c:
        r = c.post("/api/sessions/NOPE/turn/cancel", json={})
        assert r.status_code == 404
