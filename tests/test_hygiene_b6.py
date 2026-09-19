"""Hygiene B6 consumer-behavior tests.

* chat join timeout traces ``child_wait_timeout`` (partial coverage
  auditable, never mistaken for complete);
* runner parent gate honors the delegation budget override (not the
  bare singleton);
* frozen tool rows (started, never terminal) read as ``aborted`` on
  terminal delegations, untouched while live;
* escalation reads tolerate transient store errors, then report the
  error distinctly from "no record";
* session-bind failures trace ``session_bind_failed``;
* a hard-failing first turn persists buffered round-0 partials
  before the error bubble (childless narration-loss shape).
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores


@pytest.fixture(autouse=True)
def _mock_opencode_env(monkeypatch):
    # Same as tests/test_chat_loop.py: the shared chat builder runs
    # the real runtime, which must resolve the mock opencode harness
    # instead of spawning a live serve binary.
    monkeypatch.setenv("SWEAVE_MOCK_OPENCODE", "1")


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


def _chat_loop(stores: PerProjectDelegationStores, turn_timeout: float):
    from sweave.chat.loop import ChatLoop

    return ChatLoop(
        project_manager=None,
        specialist_runtime=None,
        specialist_factory=lambda name, project=None: None,
        project_dir_resolver=lambda name: None,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=turn_timeout,
    )


async def _store_for(stores: PerProjectDelegationStores, project_dir: Path):
    return await stores.for_project(project_dir)


def _runner(stores: PerProjectDelegationStores, turn_timeout: float):
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    return JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),
        delegation_stores=stores,
        turn_timeout=turn_timeout,
    )


@pytest.mark.asyncio
async def test_chat_wait_timeout_traces_partial():
    """A blocking child stuck past the cap: wait returns, and
    child_wait_timeout names the unsettled remainder."""
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-hyg-b6-chat1-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = _chat_loop(stores, turn_timeout=5.0)

    parent_id = "hyg-b6-parent-1"
    await store.add(
        Delegation(
            agent="backend", task="join", project_name="p",
            parent_task_id=parent_id, status="running", blocking=True,
            delegation_id="hyg-b6-stuck-1",
        )
    )
    trace = TraceLog(parent_id, base_dir=project_dir)
    t0 = time.monotonic()
    children = await chat._wait_for_children(
        store, parent_id, trace, timeout=0.4
    )
    assert time.monotonic() - t0 < 3.0
    assert [c.delegation_id for c in children] == ["hyg-b6-stuck-1"]
    timeouts = [
        e for e in _trace_events(project_dir, parent_id)
        if e.get("event") == "child_wait_timeout"
    ]
    assert len(timeouts) == 1
    assert timeouts[0]["pending"] == ["hyg-b6-stuck-1"]
    assert timeouts[0]["joined"] == ["hyg-b6-stuck-1"]
    assert timeouts[0]["settled"] == []


@pytest.mark.asyncio
async def test_runner_gate_uses_delegation_budget():
    """budget= override beats the runner singleton (a 100s singleton
    with a 0.3s budget times out in ~0.3s, blamed on 0.3)."""
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-hyg-b6-run1-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    runner = _runner(stores, turn_timeout=100.0)

    parent = Delegation(agent="alpha", task="parent", project_name="p")
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="child", project_name="p",
            parent_task_id=parent.delegation_id, status="running",
            blocking=True,
        )
    )
    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    t0 = time.monotonic()
    await runner._wait_for_children(parent, store, trace, budget=0.3)
    assert time.monotonic() - t0 < 5.0
    timeouts = [
        e for e in _trace_events(project_dir, parent.delegation_id)
        if e.get("event") == "children_settle_timeout"
    ]
    assert len(timeouts) == 1
    assert timeouts[0]["timeout"] == 0.3


def _trace_file(tmp_path: Path, did: str, events: list[dict]) -> Path:
    path = tmp_path / f"{did}.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_frozen_tool_rows_marked_aborted_on_terminal(tmp_path: Path):
    """tool.started with no terminal event: 'aborted' on a failed
    record, untouched while running."""
    from sweave.web.detail_view import render_detail_view

    did = "hyg-b6-frozen"
    events: list[dict] = [
        {"event": "tool.started", "callID": "c1", "tool": "bash",
         "state": {"status": "pending", "input": {"command": "sleep 99"}}},
        {"event": "status_changed", "status": "failed"},
    ]
    _trace_file(tmp_path, did, events)
    failed_view = render_detail_view(
        did, trace_dir=tmp_path, record={"status": "failed"}
    )
    (row,) = failed_view["tool_timeline"]
    assert row["status"] == "aborted"

    live_view = render_detail_view(
        did, trace_dir=tmp_path, record={"status": "running"}
    )
    (live_row,) = live_view["tool_timeline"]
    assert live_row["status"] == "pending"

    # Terminal rows keep their real end-state.
    done_events: list[dict] = [
        {"event": "tool.started", "callID": "c2", "tool": "read",
         "state": {"status": "pending"}},
        {"event": "tool.completed", "callID": "c2", "tool": "read",
         "state": {"status": "completed", "output": "x"}},
    ]
    _trace_file(tmp_path, did, done_events)
    done_view = render_detail_view(
        did, trace_dir=tmp_path, record={"status": "done"}
    )
    (done_row,) = done_view["tool_timeline"]
    assert done_row["status"] == "completed"


@pytest.mark.asyncio
async def test_resilient_read_retries_then_reports():
    """Transient store errors are ridden out; persistent ones return
    (None, error) — never conflated with 'no record'."""
    from sweave.runtime.escalation import read_record_resilient

    calls = {"n": 0}

    class _Flaky:
        async def get(self, *, delegation_id: str):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("locked")
            return {"status": "pending"}

    rec, err = await read_record_resilient(_Flaky(), "d", delay_s=0.01)
    assert rec == {"status": "pending"} and err is None
    assert calls["n"] == 3

    class _Dead:
        async def get(self, *, delegation_id: str):
            raise OSError("disk gone")

    rec, err = await read_record_resilient(_Dead(), "d", attempts=2, delay_s=0.01)
    assert rec is None and isinstance(err, OSError)


@pytest.mark.asyncio
async def test_bind_failure_traced(monkeypatch, tmp_path: Path):
    """A raising session-id setter traces session_bind_failed (the
    turn still runs; forensics can tell failed from skipped)."""
    from sweave.harness.base import AgentResult, harness_registry
    from sweave.harness.engine import ENGINE_HARNESS_NAME
    from sweave.runtime.delegation_store import Delegation as _Delegation
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist

    seen: list = []

    class _Proc:
        _session_id = "eng_bind_1"
        pid = -1

        def __init__(self, spec):
            self.spec = spec

        async def send(self, message, on_chunk=None, trace=None,
                       on_reasoning=None, on_tool=None):
            seen.append(message)
            return AgentResult(success=True, output="ok")

        async def terminate(self):
            pass

        async def wait(self):
            return AgentResult(success=True, output="")

    class _Harness:
        name = ENGINE_HARNESS_NAME

        async def spawn(self, spec):
            return _Proc(spec)

        async def attach(self, session_id, spec):
            proc = _Proc(spec)
            proc._session_id = session_id
            return proc

        def get_default_tools(self):
            return ["read"]

        async def health_check(self):
            return True

    monkeypatch.setitem(
        harness_registry._harnesses, ENGINE_HARNESS_NAME, _Harness()
    )

    def _boom(sid: str) -> None:
        raise OSError("store locked")

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    specialist = Specialist(
        name="worker", scope="project", is_orchestrator=False,
        system_prompt="", harness="sweave-engine",
    )
    delegation = _Delegation(agent="worker", task="t", model="")

    class _Trace:
        def __init__(self):
            self.events: list = []

        def append(self, name, payload=None):
            self.events.append((name, payload or {}))

    trace = _Trace()
    out, fallback = await runtime._run_engine_attempt(
        specialist=specialist,
        delegation=delegation,
        worktree_path=tmp_path,
        message="hi",
        trace=trace,  # type: ignore[arg-type]
        session_id_setter=_boom,
        project_dir=tmp_path,
        permission_roots=[],
    )
    assert (out, fallback) == ("ok", None)
    kinds = [e for e, _ in trace.events]
    assert "session_bind_failed" in kinds


@pytest.mark.asyncio
async def test_first_turn_failure_persists_partial(tmp_path: Path):
    """Childless hard failure with streamed partials: a non-final
    round-0 message keeps the narration, then the error bubble."""
    from sweave.projects import ProjectManager
    from tests.test_chat_loop import (
        _assistant_messages,
        _build_chat_loop,
        _new_session,
    )

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["unused"])
    orig = chat.runtime._send_message

    async def streaming_failure(self, body=None, trace=None, on_chunk=None,
                                on_reasoning=None, on_tool=None, **kwargs):
        if on_chunk is not None:
            on_chunk("Let me check the repo layout first...")
        if on_reasoning is not None:
            on_reasoning("planning: list files, then read")
        if on_tool is not None:
            from sweave.chat.tools import tool_event

            await on_tool(tool_event("call_p1", "read", {
                "status": "completed",
                "input": {"filePath": "notes.txt"},
                "output": "alpha",
            }))
        return "[chat error: stalled after 300s silence]"

    chat.runtime._send_message = streaming_failure  # type: ignore[assignment]
    result = await chat.run_turn(session_id=session.id, user_content="do X")
    assert "stalled" in result["content"]

    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 2
    partial, final = assistants
    assert partial.metadata["turn_round"] == 0
    assert partial.metadata["turn_final"] is False
    assert "repo layout" in partial.content
    assert "stalled" in final.content
    assert final.metadata.get("turn_final", True) is not False
    assert orig is not None  # keep linters honest about the wrapper shape
