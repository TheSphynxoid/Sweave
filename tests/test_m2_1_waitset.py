"""M2.1 wait-set tests (plan steps 4-5 done-gates).

Both waits share one rule (``JOIN_SETTLED_STATUSES`` + the
``blocking`` join flag in ``sweave/runtime/delegation_store.py``):
* only ``blocking=True`` children join the wait (fire-and-forget
  children land in the Children lane without gating);
* ``review`` counts as settled (promotion is explicit and may lag);
* an empty join set returns immediately (no deadline burn);
* the skipped set is named in a ``wait_set_scoped`` trace event
  (the skip is always auditable, never silently absorbed);
* the timeout path still emits ``children_settle_timeout``.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
    in_join_set,
    is_join_settled,
    resolve_blocking,
)


def test_join_settled_rule():
    assert is_join_settled("done")
    assert is_join_settled("failed")
    assert is_join_settled("review")
    assert not is_join_settled("queued")
    assert not is_join_settled("running")
    assert not is_join_settled(None)


def test_in_join_set_defaults_to_nonblocking():
    assert in_join_set(Delegation(agent="a", task="t", blocking=True))
    assert not in_join_set(Delegation(agent="a", task="t"))
    # Duck-typed doubles / pre-M2.1 records without the field read
    # as non-blocking (getattr-based).
    assert not in_join_set(SimpleNamespace(status="done"))


def test_resolve_blocking_explicit_wins():
    chat_parent = SimpleNamespace(kind="chat")
    task_parent = SimpleNamespace(kind="task")
    assert resolve_blocking(True, chat_parent) is True
    assert resolve_blocking(False, chat_parent) is False
    assert resolve_blocking(True, task_parent) is True
    assert resolve_blocking(False, task_parent) is False
    assert resolve_blocking(True, None) is True
    assert resolve_blocking(False, None) is False


def test_resolve_blocking_omitted_chat_parent_joins():
    # 2026-09-14 ruling: an omitted flag on a chat-turn defer joins
    # the synthesis wait-set (the orchestrator defers because it
    # needs the answer); everything else stays fire-and-forget.
    assert resolve_blocking(None, SimpleNamespace(kind="chat")) is True
    assert resolve_blocking(None, SimpleNamespace(kind="task")) is False
    assert resolve_blocking(None, None) is False
    # Duck-typed parents without kind read as non-chat (legacy).
    assert resolve_blocking(None, SimpleNamespace()) is False


def _chat_loop(stores: PerProjectDelegationStores, turn_timeout: float):
    from sweave.chat.loop import ChatLoop

    return ChatLoop(
        project_manager=None,
        specialist_runtime=None,
        specialist_factory=lambda name: None,
        project_dir_resolver=lambda name: None,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=turn_timeout,
    )


async def _store_for(stores: PerProjectDelegationStores, project_dir: Path):
    return await stores.for_project(project_dir)


def _trace_events(project_dir: Path, delegation_id: str) -> list[dict]:
    from sweave.runtime.trace_log import TraceLog

    path = TraceLog(delegation_id, base_dir=project_dir).path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _runner(stores: PerProjectDelegationStores, turn_timeout: float):
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    return JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),
        delegation_stores=stores,
        turn_timeout=turn_timeout,
    )


# ---------------------------------------------------------------------------
# ChatLoop join (step 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_join_is_blocking_only():
    """Blocking + fire-and-forget done children: only the blocking
    child is returned as synthesis input."""
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-chat1-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = _chat_loop(stores, turn_timeout=5.0)

    parent_id = "ws-chat-parent-1"
    await store.add(
        Delegation(
            agent="backend", task="join", project_name="p",
            parent_task_id=parent_id, status="done", blocking=True,
        )
    )
    await store.add(
        Delegation(
            agent="helper", task="side", project_name="p",
            parent_task_id=parent_id, status="done",
        )
    )
    children = await chat._wait_for_children(store, parent_id)
    assert [c.agent for c in children] == ["backend"]


@pytest.mark.asyncio
async def test_chat_mixed_gates_on_blocking_alone():
    """A settled blocking child + a still-running fire-and-forget
    child: the turn gates on the blocking child alone (fast
    return)."""
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-chat2-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = _chat_loop(stores, turn_timeout=5.0)

    parent_id = "ws-chat-parent-2"
    await store.add(
        Delegation(
            agent="backend", task="join", project_name="p",
            parent_task_id=parent_id, status="done", blocking=True,
        )
    )
    await store.add(
        Delegation(
            agent="helper", task="side", project_name="p",
            parent_task_id=parent_id, status="running",
        )
    )
    t0 = time.monotonic()
    children = await chat._wait_for_children(store, parent_id)
    assert time.monotonic() - t0 < 2.0
    assert [c.agent for c in children] == ["backend"]


@pytest.mark.asyncio
async def test_chat_blocking_running_child_still_gates():
    """A still-running BLOCKING child burns the deadline (bounded
    wait, then synthesize on what's terminal)."""
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-chat3-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = _chat_loop(stores, turn_timeout=2.0)

    parent_id = "ws-chat-parent-3"
    await store.add(
        Delegation(
            agent="backend", task="join", project_name="p",
            parent_task_id=parent_id, status="running", blocking=True,
        )
    )
    t0 = time.monotonic()
    children = await chat._wait_for_children(store, parent_id)
    assert 1.5 <= time.monotonic() - t0 <= 4.0
    assert [c.agent for c in children] == ["backend"]


@pytest.mark.asyncio
async def test_chat_all_fire_and_forget_returns_immediately_with_scoped_trace():
    """All-fire-and-forget turn: immediate return (no deadline
    burn) + a ``wait_set_scoped`` event naming the skipped set."""
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-chat4-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = _chat_loop(stores, turn_timeout=5.0)

    parent_id = "ws-chat-parent-4"
    await store.add(
        Delegation(
            agent="helper", task="side", project_name="p",
            parent_task_id=parent_id, status="running",
            delegation_id="ws-skipped-1",
        )
    )
    trace = TraceLog(parent_id, base_dir=project_dir)
    t0 = time.monotonic()
    children = await chat._wait_for_children(store, parent_id, trace)
    assert time.monotonic() - t0 < 1.5
    assert children == []
    scoped = [
        e for e in _trace_events(project_dir, parent_id)
        if e.get("event") == "wait_set_scoped"
    ]
    assert len(scoped) == 1
    assert scoped[0]["skipped"] == ["ws-skipped-1"]
    assert scoped[0]["joined"] == []


@pytest.mark.asyncio
async def test_chat_review_child_settles_and_joins():
    """A BLOCKING child in ``review`` settles fast and joins the
    synthesis input (promotion may lag; the request stays
    auditable)."""
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-chat5-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = _chat_loop(stores, turn_timeout=5.0)

    parent_id = "ws-chat-parent-5"
    await store.add(
        Delegation(
            agent="backend", task="join", project_name="p",
            parent_task_id=parent_id, status="review", blocking=True,
        )
    )
    t0 = time.monotonic()
    children = await chat._wait_for_children(store, parent_id)
    assert time.monotonic() - t0 < 2.0
    assert len(children) == 1
    assert children[0].status == "review"


# ---------------------------------------------------------------------------
# JobRunner parent gate (step 5 — the :898 fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_review_child_settles_without_timeout():
    """Regression for the :898 mismatch: a BLOCKING child in
    ``review`` settles the parent gate (``children_settled`` with a
    review count, no timeout). Fails on pre-M2.1 code (which only
    settled on done/failed and burned the full timeout)."""
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-run1-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    runner = _runner(stores, turn_timeout=2.0)

    parent = Delegation(agent="alpha", task="parent", project_name="p")
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="child", project_name="p",
            parent_task_id=parent.delegation_id, status="review",
            blocking=True,
        )
    )
    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    t0 = time.monotonic()
    await runner._wait_for_children(parent, store, trace)
    assert time.monotonic() - t0 < 1.5
    events = _trace_events(project_dir, parent.delegation_id)
    settled = [e for e in events if e.get("event") == "children_settled"]
    assert len(settled) == 1
    assert settled[0]["count"] == 1
    assert settled[0]["review"] == 1
    assert not [e for e in events if e.get("event") == "children_settle_timeout"]


@pytest.mark.asyncio
async def test_runner_nonblocking_child_never_gates():
    """A still-running fire-and-forget child never gates the parent
    (immediate return, no timeout event)."""
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-run2-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    runner = _runner(stores, turn_timeout=2.0)

    parent = Delegation(agent="alpha", task="parent", project_name="p")
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="side", project_name="p",
            parent_task_id=parent.delegation_id, status="running",
        )
    )
    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    t0 = time.monotonic()
    await runner._wait_for_children(parent, store, trace)
    assert time.monotonic() - t0 < 1.5
    events = _trace_events(project_dir, parent.delegation_id)
    assert not [e for e in events if e.get("event") == "children_settle_timeout"]
    assert not [e for e in events if e.get("event") == "children_settled"]


@pytest.mark.asyncio
async def test_runner_timeout_path_still_emits_event():
    """A wedged BLOCKING child still burns the bounded wait and
    emits ``children_settle_timeout`` with the join-set count."""
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-run3-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    runner = _runner(stores, turn_timeout=2.0)

    parent = Delegation(agent="alpha", task="parent", project_name="p")
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="stuck", project_name="p",
            parent_task_id=parent.delegation_id, status="running",
            blocking=True,
        )
    )
    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    t0 = time.monotonic()
    await runner._wait_for_children(parent, store, trace)
    assert 1.5 <= time.monotonic() - t0 <= 4.0
    events = _trace_events(project_dir, parent.delegation_id)
    timeouts = [e for e in events if e.get("event") == "children_settle_timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["count"] == 1
    assert timeouts[0]["timeout"] == 2.0


@pytest.mark.asyncio
async def test_runner_scoped_trace_names_skipped_set():
    """Mixed join: the gate waits on the blocking child while the
    ``wait_set_scoped`` event names joined + skipped ids."""
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-ws-run4-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    runner = _runner(stores, turn_timeout=5.0)

    parent = Delegation(agent="alpha", task="parent", project_name="p")
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="join", project_name="p",
            parent_task_id=parent.delegation_id, status="done",
            blocking=True, delegation_id="ws-joined-1",
        )
    )
    await store.add(
        Delegation(
            agent="helper", task="side", project_name="p",
            parent_task_id=parent.delegation_id, status="done",
            delegation_id="ws-skipped-1",
        )
    )
    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    await runner._wait_for_children(parent, store, trace)
    events = _trace_events(project_dir, parent.delegation_id)
    scoped = [e for e in events if e.get("event") == "wait_set_scoped"]
    assert len(scoped) == 1
    assert scoped[0]["joined"] == ["ws-joined-1"]
    assert scoped[0]["skipped"] == ["ws-skipped-1"]
    settled = [e for e in events if e.get("event") == "children_settled"]
    assert len(settled) == 1
    assert settled[0]["count"] == 1
