"""Iteration-ceiling soft cap (2026-09-16 ruling).

The ceiling carries no proof of no-progress (unlike doom /
stuckness / volume), so it no longer kills by count: a trip files
the existing keep/stop question instead —
* keep -> another full window on the same tree + resumed session
  (every ceiling hit re-asks; the human, not a counter, is the
  bound);
* stop / skip -> fail now as ``turn_stopped_by_user``;
* no store / foreign pending question / record gone -> today's
  failure path (bypass; never overwrite a live ask).

Plus the join-hold slide: a joined child with a PENDING
escalation holds the synthesis/parent gate open (deadline
slides) — no timer abandons an unanswered question.

Runner flows drive real ``JobRunner._run`` (submit + wait) with
a scripted stub runtime; chat flows drive real
``ChatLoop.run_turn`` with the canned wire + a real
EscalationStore.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import (
    Delegation,
    is_ceiling_trip_error,
)
from sweave.runtime.trace_log import TraceLog


@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old

CEILING_SENTINEL = (
    "[chat error: max_steps: max loop iterations (150) exceeded "
    "— partial work is kept]"
)
OTHER_SENTINEL = "[chat error: turn_timeout: turn_timeout_exceeded_30s]"


def test_is_ceiling_trip_error_matches_wire_contract():
    assert is_ceiling_trip_error(CEILING_SENTINEL)
    assert is_ceiling_trip_error(
        "[chat error: max_steps: max loop iterations (300) exceeded]"
    )
    assert not is_ceiling_trip_error(OTHER_SENTINEL)
    assert not is_ceiling_trip_error("[chat error: no_progress: ...]")
    assert not is_ceiling_trip_error(None)
    assert not is_ceiling_trip_error("")
    assert not is_ceiling_trip_error(123)


# ---------------------------------------------------------------------------
# Runner: real _run via submit + wait (test_supervisor_step2 pattern)
# ---------------------------------------------------------------------------


def _runner_parts(tmp_path: Path):
    from tests.test_supervisor_step2 import (
        _ScriptedRuntime,
        _esc_store,
        _runner,
        _trace_events,
        _wait_for_question,
    )

    return _ScriptedRuntime, _esc_store, _runner, _trace_events, _wait_for_question


async def _ceiling_once(kwargs):
    return CEILING_SENTINEL


async def _ok(kwargs):
    return "recovered output"


@pytest.mark.asyncio
async def test_runner_ceiling_keep_continues_same_tree(tmp_path: Path):
    """Ceiling trip -> keep/stop question quoting the ceiling ->
    keep -> second attempt on the same tree -> review."""
    _ScriptedRuntime, _esc_store, _runner, _trace_events, _wait_for_question = (
        _runner_parts(tmp_path)
    )
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_ceiling_once, _ok], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    asked = await _wait_for_question(store, d.delegation_id)
    assert "iteration ceiling" in asked["question"]
    assert (asked.get("metadata") or {}).get("ceiling") is True
    assert (asked.get("metadata") or {}).get("soft_limit") is True
    await store.answer(delegation_id=d.delegation_id, response="Keep going")
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "review"
    assert terminal.output == "recovered output"
    assert len(runtime.calls) == 2
    names = [e.get("event") for e in _trace_events(tmp_path, d.delegation_id)]
    assert "turn_ceiling_asked" in names
    assert "turn_ceiling_keep_rerun" in names


@pytest.mark.asyncio
async def test_runner_ceiling_stop_stops(tmp_path: Path):
    _ScriptedRuntime, _esc_store, _runner, _trace_events, _wait_for_question = (
        _runner_parts(tmp_path)
    )
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_ceiling_once, _ok], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    await _wait_for_question(store, d.delegation_id)
    await store.answer(delegation_id=d.delegation_id, response="Stop it")
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert terminal.error == "turn_stopped_by_user"
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_runner_ceiling_reasks_per_hit_not_per_turn(tmp_path: Path):
    """Keep grants ONE more window; the next ceiling hit asks again
    (unlike the pulsed one-shot). The human is the bound."""
    _ScriptedRuntime, _esc_store, _runner, _trace_events, _wait_for_question = (
        _runner_parts(tmp_path)
    )
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_ceiling_once, _ceiling_once, _ok], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    await _wait_for_question(store, d.delegation_id)
    await store.answer(delegation_id=d.delegation_id, response="Keep going")
    await _wait_for_question(store, d.delegation_id)
    await store.answer(delegation_id=d.delegation_id, response="Keep going")
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "review"
    assert len(runtime.calls) == 3
    names = [e.get("event") for e in _trace_events(tmp_path, d.delegation_id)]
    assert names.count("turn_ceiling_asked") == 2
    assert names.count("turn_ceiling_keep_rerun") == 2


@pytest.mark.asyncio
async def test_runner_non_ceiling_sentinel_bypasses(tmp_path: Path):
    """A non-ceiling wire death never files a ceiling question."""

    async def _silent(kwargs):
        return OTHER_SENTINEL

    _ScriptedRuntime, _esc_store, _runner, _trace_events, _wait_for_question = (
        _runner_parts(tmp_path)
    )
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_silent], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert terminal.error == OTHER_SENTINEL
    rec = await store.get(delegation_id=d.delegation_id)
    assert rec is None


@pytest.mark.asyncio
async def test_runner_pending_foreign_question_bypassed(tmp_path: Path):
    """A pending non-ceiling ask is never overwritten by the ceiling
    flow; the turn fails with today's ceiling error."""
    foreign_ready = asyncio.Event()

    async def _ceiling_after_foreign(kwargs):
        await asyncio.wait_for(foreign_ready.wait(), timeout=15)
        return CEILING_SENTINEL

    _ScriptedRuntime, _esc_store, _runner, _trace_events, _wait_for_question = (
        _runner_parts(tmp_path)
    )
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_ceiling_after_foreign], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    await store.create(
        delegation_id=d.delegation_id,
        question="Permission required: ...",
        options=["allow once", "deny"],
        kind="permission",
        audience="human",
        timeout_seconds=None,
        metadata={"requestID": "per_x"},
    )
    foreign_ready.set()
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert "max_steps" in (terminal.error or "")
    rec = await store.get(delegation_id=d.delegation_id)
    assert rec is not None and rec.get("kind") == "permission"
    assert not bool((rec.get("metadata") or {}).get("ceiling"))


@pytest.mark.asyncio
async def test_runner_parent_gate_holds_for_child_question(tmp_path: Path):
    """A join child parked on a human question holds the parent
    gate open past its deadline (no abandon)."""
    _ScriptedRuntime, _esc_store, _runner, _trace_events, _wait_for_question = (
        _runner_parts(tmp_path)
    )
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([], esc_store=store)
    runner = _runner(tmp_path, runtime)
    runner.turn_timeout = 0.4
    parent = Delegation(agent="backend", task="p", model="", project_name="p1")
    project_store = await runner._store_for(parent)
    await project_store.add(parent)
    child = Delegation(
        agent="backend",
        task="c",
        model="",
        project_name="p1",
        parent_task_id=parent.delegation_id,
        blocking=True,
        status="running",
    )
    await project_store.add(child)
    await store.create(
        delegation_id=child.delegation_id,
        question="Child needs an answer",
        options=["yes", "no"],
        kind="question",
        audience="human",
        timeout_seconds=None,
        metadata={},
    )
    trace = TraceLog(parent.delegation_id, base_dir=tmp_path / "traces")
    gate = asyncio.ensure_future(
        runner._wait_for_children(parent, project_store, trace)
    )
    await asyncio.sleep(0.7)  # past the 0.4 deadline
    assert not gate.done()  # held open, not abandoned
    await store.answer(delegation_id=child.delegation_id, response="yes")
    await project_store.update(
        child.delegation_id, status="done", completed_at=datetime.now()
    )
    await asyncio.wait_for(gate, timeout=10)
    names = [e.get("event") for e in _trace_events(tmp_path, parent.delegation_id)]
    assert "wait_join_held" in names


# ---------------------------------------------------------------------------
# Chat: real run_turn with the canned wire + a real EscalationStore
# ---------------------------------------------------------------------------


def _chat_parts():
    from tests.test_chat_loop import (
        _assistant_messages,
        _build_chat_loop,
        _new_session,
    )

    return _assistant_messages, _build_chat_loop, _new_session


def _esc_store(tmp_path: Path):
    from sweave.runtime.escalation import EscalationStore

    return EscalationStore(base_dir=tmp_path / "esc")


async def _wait_turn_delegation(chat, session_id: str) -> str:
    for _ in range(500):
        await asyncio.sleep(0.01)
        did = chat._turn_delegations.get(session_id)
        if did:
            return did
    raise AssertionError("turn delegation never registered")


async def _wait_ceiling_question(store, did: str):
    for _ in range(500):
        await asyncio.sleep(0.01)
        rec = await store.get(delegation_id=did)
        if (
            rec
            and rec.get("status") == "pending"
            and bool((rec.get("metadata") or {}).get("ceiling"))
        ):
            return rec
    raise AssertionError("ceiling question never filed")


@pytest.mark.asyncio
async def test_chat_first_turn_ceiling_keep_continues(tmp_path: Path):
    """First-turn ceiling -> keep -> continuation turn in the same
    session -> the continuation text is the final answer."""
    _assistant_messages, _build_chat_loop, _new_session = _chat_parts()
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    store = _esc_store(tmp_path)
    chat = _build_chat_loop(
        pm=pm,
        send_responses=[CEILING_SENTINEL, "continued and done"],
        escalation_store=store,
    )

    main = asyncio.ensure_future(
        chat.run_turn(session_id=session.id, user_content="do a big thing")
    )
    did = await _wait_turn_delegation(chat, session.id)
    await _wait_ceiling_question(store, did)
    await store.answer(delegation_id=did, response="Keep going")
    result = await asyncio.wait_for(main, timeout=30)
    assert result["content"] == "continued and done"
    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 1
    assert assistants[0].content == "continued and done"


@pytest.mark.asyncio
async def test_chat_first_turn_ceiling_stop_keeps_error(tmp_path: Path):
    """Stop -> today's failure path stands (the error is the reply)."""
    _assistant_messages, _build_chat_loop, _new_session = _chat_parts()
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    store = _esc_store(tmp_path)
    chat = _build_chat_loop(
        pm=pm, send_responses=[CEILING_SENTINEL], escalation_store=store
    )

    main = asyncio.ensure_future(
        chat.run_turn(session_id=session.id, user_content="do a big thing")
    )
    did = await _wait_turn_delegation(chat, session.id)
    await _wait_ceiling_question(store, did)
    await store.answer(delegation_id=did, response="Stop it")
    result = await asyncio.wait_for(main, timeout=30)
    assert result["content"] == CEILING_SENTINEL


@pytest.mark.asyncio
async def test_chat_synthesis_ceiling_keep_reruns(tmp_path: Path):
    """Synthesis ceiling -> keep -> synthesis re-runs -> round 1
    final carries the recovered text (round 0 narration intact)."""
    _assistant_messages, _build_chat_loop, _new_session = _chat_parts()
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    store = _esc_store(tmp_path)
    chat = _build_chat_loop(
        pm=pm,
        send_responses=[
            "I'll ask backend to do that.",
            CEILING_SENTINEL,
            "Backend did the thing.",
        ],
        escalation_store=store,
    )
    orig = chat.runtime._send_message
    injected: list[bool] = []

    async def injecting_send(self, body=None, trace=None, on_chunk=None,
                             on_reasoning=None, **kwargs):
        out = await orig(self, body, trace, on_chunk, on_reasoning, **kwargs)
        if not injected:
            injected.append(True)
            project_store = await chat.delegation_stores.for_project(tmp_path)
            for rec in project_store.list():
                if rec.kind == "chat":
                    await project_store.add(Delegation(
                        delegation_id="child-backend-1",
                        task_id="child-backend-1",
                        agent="backend",
                        model="hy3",
                        task="create hello.py",
                        parent_task_id=rec.delegation_id,
                        project_name="demo",
                        status="done",
                        output="created hello.py printing OK",
                        completed_at=datetime.now(),
                    ))
                    break
        return out

    chat.runtime._send_message = injecting_send  # type: ignore[assignment]
    main = asyncio.ensure_future(
        chat.run_turn(session_id=session.id, user_content="ask backend to do X")
    )
    did = await _wait_turn_delegation(chat, session.id)
    await _wait_ceiling_question(store, did)
    await store.answer(delegation_id=did, response="Keep going")
    result = await asyncio.wait_for(main, timeout=30)
    assert result["content"] == "Backend did the thing."
    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 2
    assert assistants[0].content == "I'll ask backend to do that."
    assert assistants[1].content == "Backend did the thing."


@pytest.mark.asyncio
async def test_chat_join_wait_holds_for_child_question(tmp_path: Path):
    """A join child parked on a human question holds the synthesis
    wait open past its deadline (no abandon)."""
    _assistant_messages, _build_chat_loop, _new_session = _chat_parts()
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    del session
    store = _esc_store(tmp_path)
    chat = _build_chat_loop(pm=pm, escalation_store=store)
    project_store = await chat.delegation_stores.for_project(tmp_path)
    child = Delegation(
        agent="backend",
        task="c",
        model="",
        project_name="demo",
        parent_task_id="parent-chat-1",
        blocking=True,
        status="running",
    )
    await project_store.add(child)
    await store.create(
        delegation_id=child.delegation_id,
        question="Child needs an answer",
        options=["yes", "no"],
        kind="question",
        audience="human",
        timeout_seconds=None,
        metadata={},
    )
    trace = TraceLog("parent-chat-1", base_dir=tmp_path / "traces")
    wait = asyncio.ensure_future(
        chat._wait_for_children(project_store, "parent-chat-1", trace, timeout=0.4)
    )
    await asyncio.sleep(0.7)  # past the deadline
    assert not wait.done()  # held open, not abandoned
    await store.answer(delegation_id=child.delegation_id, response="yes")
    await project_store.update(
        child.delegation_id, status="done", completed_at=datetime.now()
    )
    settled = await asyncio.wait_for(wait, timeout=10)
    assert [c.delegation_id for c in settled] == [child.delegation_id]
