"""JobRunner tests with a stubbed DelegateTaskTool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.delegation_store import DelegationStore
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.trace_log import read_trace
from sweave.tools import DelegationResult
from sweave.web.events import WSEventBus


class StubDelegateTool:
    """Stand-in for DelegateTaskTool that records calls and returns a fixed result."""

    def __init__(self, result: DelegationResult | None = None, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self._fail = fail
        # Mimic the exception path
        self.exception: Exception | None = None

    async def execute(
        self,
        agent: str,
        task: str,
        model: str | None = None,
        task_id: str | None = None,
    ) -> DelegationResult:
        self.calls.append(
            {"agent": agent, "task": task, "model": model, "task_id": task_id}
        )
        if self.exception is not None:
            raise self.exception
        if self._result is not None:
            return self._result
        if self._fail:
            return DelegationResult(
                success=False, agent=agent, task_id=task_id or "x",
                output="", error="forced failure",
            )
        return DelegationResult(
            success=True, agent=agent, task_id=task_id or "x", output="ok"
        )


@pytest.mark.asyncio
async def test_submit_returns_queued_delegation(tmp_path: Path):
    bus = WSEventBus()
    store = DelegationStore()
    tool = StubDelegateTool()
    runner = JobRunner(tool, store, event_bus=bus, traces_dir=tmp_path)

    d = await runner.submit("backend", "hello", model="m1")
    assert d.status == "queued"
    assert d.agent == "backend"
    assert d.model == "m1"
    assert d.task == "hello"
    assert store.get(d.delegation_id) is d


@pytest.mark.asyncio
async def test_submit_emits_status_changed_on_bus(tmp_path: Path):
    bus = WSEventBus()
    store = DelegationStore()
    tool = StubDelegateTool()
    runner = JobRunner(tool, store, event_bus=bus, traces_dir=tmp_path)

    seen: list[dict[str, Any]] = []

    class WS:
        async def send_text(self, text: str) -> None:
            import json
            seen.append(json.loads(text))

    await bus.subscribe(WS())  # type: ignore[arg-type]
    d = await runner.submit("backend", "x")
    # Wait for terminal
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status in {"done", "failed"}

    statuses = [
        e["data"]["status"] for e in seen if e["event"] == "delegation.status_changed"
    ]
    assert "queued" in statuses
    assert "running" in statuses
    assert final.status in statuses


@pytest.mark.asyncio
async def test_submit_writes_trace_file(tmp_path: Path):
    bus = WSEventBus()
    store = DelegationStore()
    tool = StubDelegateTool()
    runner = JobRunner(tool, store, event_bus=bus, traces_dir=tmp_path)

    d = await runner.submit("backend", "x")
    await runner.wait(d.delegation_id, timeout=5)
    events = read_trace(d.delegation_id, base_dir=tmp_path)
    assert len(events) >= 3
    assert [e["event"] for e in events[:3]] == [
        "status_changed", "status_changed", "prompt_sent",
    ]


@pytest.mark.asyncio
async def test_successful_run_ends_in_done(tmp_path: Path):
    bus = WSEventBus()
    store = DelegationStore()
    tool = StubDelegateTool(DelegationResult(
        success=True, agent="backend", task_id="t", output="OK",
    ))
    runner = JobRunner(tool, store, event_bus=bus, traces_dir=tmp_path)

    d = await runner.submit("backend", "x")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "done"
    assert final.output == "OK"
    assert tool.calls and tool.calls[0]["task"] == "x"


@pytest.mark.asyncio
async def test_failed_run_ends_in_failed(tmp_path: Path):
    bus = WSEventBus()
    store = DelegationStore()
    tool = StubDelegateTool(fail=True)
    runner = JobRunner(tool, store, event_bus=bus, traces_dir=tmp_path)

    d = await runner.submit("backend", "x")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "failed"
    assert final.error == "forced failure"


@pytest.mark.asyncio
async def test_exception_in_tool_ends_in_failed(tmp_path: Path):
    bus = WSEventBus()
    store = DelegationStore()
    tool = StubDelegateTool()
    tool.exception = RuntimeError("kaboom")
    runner = JobRunner(tool, store, event_bus=bus, traces_dir=tmp_path)

    d = await runner.submit("backend", "x")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "failed"
    assert "kaboom" in (final.error or "")


@pytest.mark.asyncio
async def test_submit_without_bus_works(tmp_path: Path):
    """Event bus is optional; the runner works without one."""
    store = DelegationStore()
    tool = StubDelegateTool()
    runner = JobRunner(tool, store, event_bus=None, traces_dir=tmp_path)

    d = await runner.submit("backend", "x")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "done"
