"""Live child forwarding (view plan Amendment 2026-09-16, step 4c-backend).

``JobRunner._run`` now passes ``on_chunk`` / ``on_reasoning`` /
``on_tool`` into ``SpecialistRuntime.run`` for child turns (the
freeze's second half: mid-turn nothing ticked). Locked contract:

* ADDITIVE WS event names: ``specialist.delta`` /
  ``specialist.thinking`` / ``specialist.tool`` — same shapes as the
  chat twins but keyed by the CHILD delegation_id (no ``session_id``:
  a child turn belongs to its parent chat via the record, not a
  Session bubble; chat consumers were never affected — the standing
  WS-vocab ruling).
* Text/thinking coalesce on child-keyed coalescers (same doctrine as
  the chat loop, 200ms / 64-char); tool events ride one per transition
  (latest-status-wins per callID) with ``MAX_TOOLS_PER_MESSAGE`` capping
  NEW callIDs.
* The coalescers close on every attempt exit (choose-attempt semantics:
  forwarding lives per attempt; the finally is inside ``_attempt``).
* Never fails the turn: publish blowups log + continue; a throwaway
  callback cannot poison the runtime path (the runtime guards
  callbacks itself).

Backend half of the 3-5s poll fallback contract: the UI polls while
``running/queued`` (4c-frontend); these events make an OPEN detail
refetch fresher than the poll alone (mid-turn tail text + tool rows
without waiting a tick).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sweave.runtime.job_runner import JobRunner
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist
from tests.conftest import fake_worktree_manager_factory


def _wt_factory():
    return fake_worktree_manager_factory()[0]


@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    old = __import__("os").environ.get("SWEAVE_MOCK_OPENCODE")
    __import__("os").environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            __import__("os").environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            __import__("os").environ["SWEAVE_MOCK_OPENCODE"] = old


class _FakeDelegateTool:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, agent, task, model=None, task_id=None):
        from sweave.tools import DelegationResult

        self.calls.append({"agent": agent, "task": task})
        return DelegationResult(
            success=True, agent=agent, task_id=task_id or "stub",
            output="legacy", error=None,
        )


def _project_resolver(tmp_path: Path):
    def _r(name):
        return tmp_path if name else None

    return _r


def _make_runtime_capture_callbacks():
    """Runtime whose ``_send_message`` mock records the callbacks it
    received AND pushes canned stream data through them (so a test can
    see the full forwarding loop end-to-end)."""
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    sent: list[dict[str, Any]] = []

    async def fake_send(
        self,
        body=None,
        trace=None,
        on_chunk=None,
        on_reasoning=None,
        on_tool=None,
        **kwargs,
    ):
        sent.append(
            {
                "on_chunk": on_chunk,
                "on_reasoning": on_reasoning,
                "on_tool": on_tool,
            }
        )
        if on_chunk is not None:
            await _maybe_await(on_chunk("hello "))
            await _maybe_await(on_chunk("child world"))
        if on_tool is not None:
            await _maybe_await(
                on_tool(
                    {
                        "callID": "c1",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "output": "ok",
                            "input": {"command": "pytest -q"},
                        },
                    }
                )
            )
        return "done"

    runtime._send_message = fake_send  # type: ignore[assignment]
    return runtime, sent


async def _maybe_await(cb_result: Any) -> None:
    if hasattr(cb_result, "__await__"):
        await cb_result


def _make_runner(runtime, tmp_path: Path, event_bus=None) -> JobRunner:
    from sweave.runtime.delegation_store import PerProjectDelegationStores

    return JobRunner(
        delegate_tool=_FakeDelegateTool(),
        delegation_stores=PerProjectDelegationStores(),
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=lambda name, project_name=None: Specialist(
            name=name, scope="project", is_orchestrator=False,
            system_prompt="", harness="opencode", current_model=None,
        ),
        worktree_manager_factory=_wt_factory(),
        event_bus=event_bus,
    )


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event: str, data: dict) -> None:
        self.events.append((event, data))


@pytest.mark.asyncio
async def test_child_turn_forwards_all_three_callbacks(tmp_path: Path):
    runtime, sent = _make_runtime_capture_callbacks()
    runner = _make_runner(runtime, tmp_path)
    d = await runner.submit(agent="backend", task="live task", model=None)
    await runner.wait(d.delegation_id, timeout=10)
    assert len(sent) == 1
    assert sent[0]["on_chunk"] is not None
    assert sent[0]["on_reasoning"] is not None
    assert sent[0]["on_tool"] is not None


@pytest.mark.asyncio
async def test_child_event_names_and_child_id_keying(tmp_path: Path):
    """Published payloads carry the CHILD delegation id (never a
    session_id) under the new additive names."""
    runtime, _sent = _make_runtime_capture_callbacks()
    bus = _Bus()
    runner = _make_runner(runtime, tmp_path, event_bus=bus)
    d = await runner.submit(agent="backend", task="live task", model=None)
    await runner.wait(d.delegation_id, timeout=10)

    deltas = [d for e, d in bus.events if e == "specialist.delta"]
    thinking = [d for e, d in bus.events if e == "specialist.thinking"]
    tools = [d for e, d in bus.events if e == "specialist.tool"]
    assert deltas, "coalesced child text should publish"
    assert all(dd["delegation_id"] == d.delegation_id for dd in deltas)
    assert all(dd["delegation_id"] == d.delegation_id for dd in thinking)
    assert all(dd["delegation_id"] == d.delegation_id for dd in tools)
    assert "session_id" not in deltas[0]
    # The two pushed chunks coalesced into >=1 delta carrying the text.
    joined = "".join(dd["text"] for dd in deltas)
    assert joined == "hello child world"
    # Tool row shape mirrors the chat compact rows.
    assert tools[0]["tool"]["tool"] == "bash"
    assert tools[0]["tool"]["status"] == "completed"
    assert tools[0]["tool"]["summary"]


@pytest.mark.asyncio
async def test_latest_status_wins_per_callid(tmp_path: Path):
    runtime, _sent = _make_runtime_capture_callbacks()
    # Override the capture to emit two transitions of the same callID.
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.serve_runner import ServeRunnerRegistry

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_tool=None, **kwargs):
        if on_tool is not None:
            await on_tool(
                {"callID": "c1", "tool": "bash", "state": {"status": "running", "input": {"command": "x"}}}
            )
            await on_tool(
                {"callID": "c1", "tool": "bash", "state": {"status": "completed", "output": "ok"}}
            )
        return "done"

    runtime._send_message = fake_send  # type: ignore[assignment]
    bus = _Bus()
    runner = _make_runner(runtime, tmp_path, event_bus=bus)
    d = await runner.submit(agent="backend", task="tool task", model=None)
    await runner.wait(d.delegation_id, timeout=10)
    tools = [d for e, d in bus.events if e == "specialist.tool"]
    assert [t["tool"]["status"] for t in tools] == ["running", "completed"]
    assert all(t["tool"]["callID"] == "c1" for t in tools)


@pytest.mark.asyncio
async def test_callbacks_are_optional_and_publish_never_fails_the_turn(
    tmp_path: Path,
):
    """Without an event bus the forward callbacks must be no-ops (a
    turn completes unchanged); a throwing bus never fails the turn."""
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

    async def fake_send(self, body=None, trace=None, on_chunk=None, **kwargs):
        if on_chunk is not None:
            await on_chunk("streamed")
        return "ok"

    runtime._send_message = fake_send  # type: ignore[assignment]

    class _ThrowingBus:
        async def publish(self, event, data):
            if event.startswith("specialist."):
                raise RuntimeError("bus down")
            return None

    runner = _make_runner(runtime, tmp_path, event_bus=_ThrowingBus())
    d = await runner.submit(agent="backend", task="noisy", model=None)
    rec = await runner.wait(d.delegation_id, timeout=10)
    assert rec is not None
    # Implementation delegations settle to review (M1.4 ruling); the
    # honest assertion is: the turn COMPLETED (not failed) despite the
    # throwing bus on the forwarding path.
    assert rec.status in ("review", "done")
    assert rec.output == "ok"
