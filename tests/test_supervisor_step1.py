"""Supervisor step 1: one clock owner + corpse guard.

Incident f774d84b (2026-09-15): a healthy 30-minute engine turn
died with output "" because THREE clocks enforced the same 1800s
independently (outer `_bounded_turn`, harness httpx, sidecar
loop) — the outer granted a beacon extension over an inner
attempt that had already died, and the keep/stop question never
fired.

Step 1 (no supervision yet, just clock hygiene):
* the runtime forwards the per-delegation budget into engine
  message metadata (`turn_timeout`) on BOTH the runner and chat
  paths, so harness + sidecar enforce the outer value;
* `_bounded_turn` (and the chat loop's twin wait) collect an
  already-done task immediately (`turn_corpse_collected`) instead
  of consuming extensions/questions over a corpse.

Default values are byte-identical in behavior (1800=1800) — the
race just disappears, and overlay raises work end-to-end.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


def test_harness_turn_timeout_reads_metadata():
    from sweave.harness.base import Message
    from sweave.harness.engine import _turn_timeout

    assert _turn_timeout(Message(type="user", content="x", metadata={"turn_timeout": 7200})) == 7200
    # Absent / garbage / bool / non-positive all fall back to 1800.
    assert _turn_timeout(Message(type="user", content="x", metadata={})) == 1800.0
    assert _turn_timeout(Message(type="user", content="x", metadata={"turn_timeout": "soon"})) == 1800.0
    assert _turn_timeout(Message(type="user", content="x", metadata={"turn_timeout": True})) == 1800.0
    assert _turn_timeout(Message(type="user", content="x", metadata={"turn_timeout": -5})) == 1800.0


def _register_fake_engine(monkeypatch, seen):
    from sweave.harness.base import harness_registry
    from sweave.harness.engine import ENGINE_HARNESS_NAME

    class _Proc:
        _session_id = "eng_step1"

        async def send(self, message, **kw):
            seen.append(message)
            from sweave.harness.base import AgentResult

            return AgentResult(success=True, output="done")

    class _Harness:
        name = ENGINE_HARNESS_NAME

        async def spawn(self, spec):
            return _Proc()

        async def attach(self, session_id, spec):
            return _Proc()

        def get_default_tools(self):
            return ["read"]

    monkeypatch.setitem(harness_registry._harnesses, ENGINE_HARNESS_NAME, _Harness())


def _project_specialist():
    from sweave.runtime.specialist_store import Specialist

    return Specialist(
        name="worker", scope="project", is_orchestrator=False,
        system_prompt="", harness="sweave-engine",
    )


def _project_delegation(**kw):
    from sweave.runtime.delegation_store import Delegation

    base = dict(agent="worker", task="do it", project_name="p")
    base.update(kw)
    return Delegation(**base)


def _project_runtime(tmp_path: Path):
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    return SpecialistRuntime(runners=ServeRunnerRegistry())


@pytest.mark.asyncio
async def test_run_forwards_budget_into_engine_metadata(monkeypatch, tmp_path: Path):
    """The per-delegation budget rides the engine message (P1)."""
    from sweave.runtime.trace_log import TraceLog

    seen: list = []
    _register_fake_engine(monkeypatch, seen)
    runtime = _project_runtime(tmp_path)
    d = _project_delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    out = await runtime.run(
        specialist=_project_specialist(),
        delegation=d,
        worktree_path=tmp_path,
        message="do it",
        trace=trace,
        project_dir=tmp_path,
        turn_timeout=7200.0,
    )
    trace.close()
    assert out == "done"
    assert seen and seen[0].metadata.get("turn_timeout") == 7200.0


@pytest.mark.asyncio
async def test_run_without_budget_keeps_harness_default(monkeypatch, tmp_path: Path):
    """None = metadata absent = harness 1800 fallback (legacy/tests)."""
    from sweave.runtime.trace_log import TraceLog

    seen: list = []
    _register_fake_engine(monkeypatch, seen)
    runtime = _project_runtime(tmp_path)
    d = _project_delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    await runtime.run(
        specialist=_project_specialist(),
        delegation=d,
        worktree_path=tmp_path,
        message="do it",
        trace=trace,
        project_dir=tmp_path,
    )
    trace.close()
    assert seen and "turn_timeout" not in seen[0].metadata


def _runner():
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner

    return JobRunner(
        delegate_tool=None,  # type: ignore[arg-type]
        delegation_stores=PerProjectDelegationStores(),
        turn_timeout=0.05,
    )


@pytest.mark.asyncio
async def test_bounded_turn_collects_corpse_without_extension(monkeypatch, tmp_path: Path):
    """Done-at-expiry collects immediately: no extension consumed,
    no soft question, traced distinctly (incident regression pin).

    The real race (inner death landing exactly as the outer
    expires) is timing-nondeterministic, so the test stages it
    honestly: a `wait_for` double that lets the quick task finish
    and THEN reports the outer expiry — the exact ordering the
    guard exists for.
    """
    from sweave.runtime.trace_log import TraceLog

    async def quick():
        return "late result"

    real_wait_for = asyncio.wait_for

    async def staged_expiry(fut, timeout):
        try:
            await real_wait_for(asyncio.shield(fut), 5.0)
        except asyncio.TimeoutError:
            raise
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", staged_expiry)
    runner = _runner()
    d = _project_delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    ok, output = await runner._bounded_turn(quick(), d, trace)
    trace.close()
    assert (ok, output) == (True, "late result")
    path = TraceLog(d.delegation_id, base_dir=tmp_path / "traces").path
    import json

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    names = [e.get("event") for e in events]
    assert "turn_corpse_collected" in names
    assert "turn_extended" not in names
    assert "turn_timeout" not in names


@pytest.mark.asyncio
async def test_bounded_turn_corpse_exception_propagates(monkeypatch, tmp_path: Path):
    """A done task that raised surfaces like a live failure (the
    outer handler owns it) — never swallowed into a timeout."""
    from sweave.runtime.trace_log import TraceLog

    async def quick_boom():
        raise RuntimeError("inner died")

    real_wait_for = asyncio.wait_for

    async def staged_expiry(fut, timeout):
        try:
            await real_wait_for(asyncio.shield(fut), 5.0)
        except BaseException:
            pass
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", staged_expiry)
    runner = _runner()
    d = _project_delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    with pytest.raises(RuntimeError, match="inner died"):
        await runner._bounded_turn(quick_boom(), d, trace)
    trace.close()


@pytest.mark.asyncio
async def test_bounded_turn_live_task_still_times_out(tmp_path: Path):
    """The guard changes nothing for genuinely live tasks."""
    from sweave.runtime.trace_log import TraceLog

    async def slow():
        await asyncio.sleep(10)
        return "never"

    runner = _runner()
    d = _project_delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    ok, _ = await runner._bounded_turn(slow(), d, trace)
    trace.close()
    # No escalation store wired: no hold, no beacon, no soft
    # asker — straight timeout failure.
    assert ok is False


@pytest.mark.asyncio
async def test_chat_loop_collects_corpse_without_cancel(monkeypatch, tmp_path: Path):
    """Same nested clocks on the chat path (incident f774d84b):
    a finished orchestrator turn is collected, never cancelled
    as timed out. Staged race like the runner pin."""
    from sweave.chat.loop import ChatLoop
    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
    from sweave.runtime.trace_log import TraceLog

    async def quick_run(**kw):
        return "chat reply"

    class _Runtime:
        async def run(self, **kw):
            return await quick_run()

    pm = ProjectManager(base_path=tmp_path / "projects")
    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=_Runtime(),  # type: ignore[arg-type]
        specialist_factory=lambda agent_name, project_name=None: None,
        project_dir_resolver=lambda name: tmp_path,
        delegation_stores=PerProjectDelegationStores(),
        turn_timeout=0.05,
        model_resolver=lambda agent, project=None: "m",
    )
    real_wait_for = asyncio.wait_for

    async def staged_expiry(fut, timeout):
        try:
            await real_wait_for(asyncio.shield(fut), 5.0)
        except asyncio.TimeoutError:
            raise
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", staged_expiry)
    from sweave.runtime.specialist_store import Specialist

    d = Delegation(agent="orchestrator", task="hi", project_name=None)
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    out = await chat._run_orchestrator_turn(
        specialist=Specialist(name="orchestrator", is_orchestrator=True),
        delegation=d,
        worktree_path=tmp_path,
        message="hi",
        trace=trace,  # type: ignore[arg-type]
        model_str=None,
        session_id_getter=lambda: None,
        session_id_setter=lambda sid: None,
    )
    trace.close()
    assert out == "chat reply"
