"""Stop-button fix: session id persisted at bind time, not settle.

Incident 2026-09-17 (session Sweave-20260916-215042-3dba3a): the
specialist kept working — and committed — for 3 minutes after Stop
because cancel reads the STORE mid-run while ``engine_session_id``
only rode the settle-time write. The runtime now fires
``on_session_bound`` as soon as the turn's session binds (create or
resume, both harnesses); JobRunner/ChatLoop persist it immediately.

* runtime fires the hook with the bound id (mock opencode path);
* a throwing hook never fails the turn;
* JobRunner persists the hooked id to the store record.

Follow-up 2026-09-18 (session Sweave-20260918-021652-374bd3):
the ENGINE path persisted the bind only after ``process.send()``
returned, so a mid-turn Stop cancelled the send before the bind
ran — the record kept ``engine_session_id=None``, the abort found
nothing (``turn_killed: no_live_turn``) and the sidecar turn kept
burning. The engine bind now persists pre-send (mirroring the
opencode path); the tests below pin the mid-run record on a hung
send for both resume and spawn.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest


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


def _specialist(name: str):
    from sweave.runtime.specialist_store import Specialist

    return Specialist(
        name=name,
        scope="project",
        is_orchestrator=False,
        system_prompt="p",
        harness="opencode",
    )


def _mock_runtime(monkeypatch):
    """Real SpecialistRuntime on a mock serve (no subprocess)."""
    from unittest.mock import MagicMock

    from sweave.harness.opencode import OpenCodeProcess
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

    async def fake_send_message(self, body=None, trace=None, on_chunk=None,
                                on_reasoning=None, **kwargs):
        return "task-output"

    async def spy_send(self, message, on_chunk=None, trace=None,
                       trace_reasoning=False):
        if on_chunk is not None:
            on_chunk("ok")
        return MagicMock()

    runtime._send_message = fake_send_message  # type: ignore[assignment]
    monkeypatch.setattr(OpenCodeProcess, "send", spy_send)
    return runtime


@pytest.mark.asyncio
async def test_real_runtime_fires_hook_with_bound_id(
    tmp_path: Path, monkeypatch
):
    from sweave.runtime.delegation_store import Delegation
    from sweave.runtime.trace_log import TraceLog

    runtime = _mock_runtime(monkeypatch)
    bound: list = []

    async def _hook(sid: str) -> None:
        bound.append(sid)

    spec = _specialist("hooky")
    d = Delegation(agent="hooky", task="do it", project_name="shop")
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    out = await runtime.run(
        specialist=spec,
        delegation=d,
        worktree_path=tmp_path,
        message=d.task,
        trace=trace,
        on_session_bound=_hook,
    )
    trace.close()
    assert out == "task-output"
    assert len(bound) == 1
    assert bound[0].startswith("ses_")
    assert d.engine_session_id == bound[0]


@pytest.mark.asyncio
async def test_throwing_hook_never_fails_turn(tmp_path: Path, monkeypatch):
    from sweave.runtime.delegation_store import Delegation
    from sweave.runtime.trace_log import TraceLog

    runtime = _mock_runtime(monkeypatch)

    def _boom(sid: str):
        raise RuntimeError("store down")

    spec = _specialist("hooky")
    d = Delegation(agent="hooky", task="do it", project_name="shop")
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    out = await runtime.run(
        specialist=spec,
        delegation=d,
        worktree_path=tmp_path,
        message=d.task,
        trace=trace,
        on_session_bound=_boom,
    )
    trace.close()
    assert out == "task-output"


def _hook_invoking_stub():
    """Stub runtime that fires the hook the way a live bind would."""

    class _HookRuntime:
        async def run(self, **kwargs):
            hook = kwargs.get("on_session_bound")
            assert hook is not None, "run() must receive on_session_bound"
            res = hook("eng_hook_1")
            if hasattr(res, "__await__"):
                await res
            kwargs["delegation"].engine_session_id = "eng_hook_1"
            return "stub output"

    return _HookRuntime()


def _make_runner(tmp_path: Path, runtime: Any):
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner
    from tests.conftest import fake_worktree_manager_factory
    from tests.test_job_runner import StubDelegateTool

    return JobRunner(
        delegate_tool=StubDelegateTool(),
        delegation_stores=PerProjectDelegationStores(),
        event_bus=None,
        traces_dir=tmp_path / "traces",
        project_dir_resolver=lambda name: tmp_path,
        specialist_runtime=runtime,
        specialist_factory=lambda name, project=None: _specialist(name),
        worktree_manager_factory=fake_worktree_manager_factory()[0],
    )


@pytest.mark.asyncio
async def test_bind_time_session_id_reaches_store_record(tmp_path: Path):
    """The hooked id lands on the store record — cancel finds it mid-run."""
    runner = _make_runner(tmp_path, _hook_invoking_stub())
    d = await runner.submit("alpha", "do work", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=10)
    assert final is not None and final.status == "review"

    store = await runner.stores.for_project(tmp_path)
    rec = store.get(d.delegation_id)
    assert rec is not None
    assert rec.engine_session_id == "eng_hook_1"


def _engine_specialist(name: str):
    from sweave.runtime.specialist_store import Specialist

    return Specialist(
        name=name,
        scope="project",
        is_orchestrator=False,
        system_prompt="p",
        harness="sweave-engine",
    )


class _EngineAttemptResult:
    metadata: dict = {}
    success = True
    output = "engine-output"
    error = None


class _HungEngineProcess:
    """Fake sidecar process: sid bound at attach/spawn, send hangs."""

    def __init__(self, session_id: str, release: asyncio.Event):
        self._session_id = session_id
        self._release = release

    async def send(
        self,
        message,
        on_chunk=None,
        trace=None,
        trace_reasoning=False,
        on_reasoning=None,
        on_tool=None,
    ):
        if on_chunk is not None:
            on_chunk("ok")
        await self._release.wait()
        return _EngineAttemptResult()


async def _mid_run_engine_sid(
    tmp_path: Path, monkeypatch, *, resume_sid: str | None
) -> tuple[str, Any]:
    """Drive one engine attempt with a hung send; return the bound sid.

    Mirrors production wiring: the record is pre-added (chat loop
    ``store.add``) and the hook is the loop's bind-time
    ``store.update``. Returns (bound sid, trace dir) once the turn
    settles; asserts the mid-run record inline.
    """
    from sweave.harness.engine import SweaveEngineHarness
    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.trace_log import TraceLog

    release = asyncio.Event()
    hook_calls: list = []
    hook_fired = asyncio.Event()
    sid = resume_sid or "eng_spawn_midrun"

    async def fake_attach(self, session_id, spec):
        return _HungEngineProcess(sid, release)

    async def fake_spawn(self, spec):
        return _HungEngineProcess(sid, release)

    monkeypatch.setattr(SweaveEngineHarness, "attach", fake_attach)
    monkeypatch.setattr(SweaveEngineHarness, "spawn", fake_spawn)

    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    spec = _engine_specialist("eng-midrun")
    d = Delegation(agent="eng-midrun", task="do it", project_name="shop")
    await store.add(d)

    async def _hook(bound_sid: str) -> None:
        await store.update(d.delegation_id, engine_session_id=bound_sid)
        hook_calls.append(bound_sid)
        hook_fired.set()

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
    task = asyncio.ensure_future(
        runtime._run_engine_attempt(
            specialist=spec,
            delegation=d,
            worktree_path=tmp_path,
            message=d.task,
            trace=trace,
            session_id_getter=(lambda: resume_sid),
            on_session_bound=_hook,
        )
    )
    # The regression: pre-fix the hook fires only after send
    # returns, so this times out with the send still hung.
    await asyncio.wait_for(hook_fired.wait(), timeout=5)
    rec = store.get(d.delegation_id)
    assert rec is not None
    assert rec.engine_session_id == sid
    assert d.engine_session_id == sid
    release.set()
    out, err = await asyncio.wait_for(task, timeout=5)
    trace.close()
    assert (out, err) == ("engine-output", None)
    assert hook_calls == [sid]
    return sid, tmp_path / "traces"


@pytest.mark.asyncio
async def test_engine_resume_persists_sid_before_send(
    tmp_path: Path, monkeypatch
):
    """Resumed engine turn: mid-run record carries the sid (f6b2 case)."""
    sid, traces_dir = await _mid_run_engine_sid(
        tmp_path, monkeypatch, resume_sid="eng_resume_midrun"
    )
    resumed = [
        line
        for line in (traces_dir / f"{_last_delegation(traces_dir)}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if '"session_resumed"' in line and sid in line
    ]
    assert len(resumed) == 1


@pytest.mark.asyncio
async def test_engine_spawn_persists_sid_before_send(
    tmp_path: Path, monkeypatch
):
    """Spawned engine turn: same bind-time guarantee on the new path."""
    sid, traces_dir = await _mid_run_engine_sid(
        tmp_path, monkeypatch, resume_sid=None
    )
    assert sid == "eng_spawn_midrun"
    created = [
        line
        for line in (traces_dir / f"{_last_delegation(traces_dir)}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if '"session_created"' in line and sid in line
    ]
    assert len(created) == 1


def _last_delegation(traces_dir: Path) -> str:
    files = sorted(traces_dir.glob("*.jsonl"))
    assert files, "expected a trace file"
    return files[-1].stem
