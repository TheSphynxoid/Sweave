"""Supervisor step 2: pulsed-but-dead turns route to the human.

Incident f774d84b (2026-09-15): a healthy 30-minute turn died
with output "" and nobody was asked. Now: a failure (timeout OR
wire-death sentinel) with trace pulses inside the beacon window
files ONE keep/stop question (re-run once on keep — same tree,
resumed session — or stop); silent deaths fail straight, and no
second question ever files.

All driving goes through real ``JobRunner._run`` (submit + wait)
with a scripted stub runtime — no mocks of the code under test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from tests.conftest import fake_worktree_manager_factory

SENTINEL = "[chat error: turn_timeout: turn_timeout_exceeded_30s]"


class _ScriptedRuntime:
    """Stub specialist_runtime: scripted run() behaviors + wired store."""

    def __init__(self, behaviors, esc_store=None):
        self._behaviors = behaviors
        self.calls: list = []
        self.escalation_store = esc_store

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        behavior = self._behaviors[min(len(self.calls) - 1, len(self._behaviors) - 1)]
        return await behavior(kwargs)


async def _sentinel_with_pulse(kwargs):
    kwargs["trace"].append("tool.completed", {"tool": "bash"})
    kwargs["trace"].append("reasoning", {"text": "still working"})
    return SENTINEL


async def _ok(kwargs):
    return "recovered output"


async def _sentinel_silent(kwargs):
    return SENTINEL


def _runner(tmp_path: Path, runtime) -> JobRunner:
    factory, _ = fake_worktree_manager_factory(tmp_path / "wt-root")
    return JobRunner(
        delegate_tool=None,  # type: ignore[arg-type]
        delegation_stores=PerProjectDelegationStores(),
        project_dir_resolver=lambda name: tmp_path,
        traces_dir=tmp_path / "traces",
        specialist_runtime=runtime,  # type: ignore[arg-type]
        specialist_factory=lambda agent, project=None: None,
        turn_timeout=30.0,
        worktree_manager_factory=factory,
    )


def _esc_store(tmp_path: Path):
    from sweave.runtime.escalation import EscalationStore

    return EscalationStore(base_dir=tmp_path / "esc")


async def _wait_for_question(store, delegation_id: str, timeout: float = 15.0):
    async def _poll():
        while True:
            rec = await store.get(delegation_id=delegation_id)
            if rec is not None and rec.get("status") == "pending":
                return rec
            await asyncio.sleep(0.1)

    return await asyncio.wait_for(_poll(), timeout)


def _trace_events(tmp_path: Path, delegation_id: str) -> list[dict]:
    from sweave.runtime.trace_log import TraceLog

    path = TraceLog(delegation_id, base_dir=tmp_path / "traces").path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_pulsed_wire_death_asks_and_keep_reruns(tmp_path: Path):
    """Incident replay: pulsed sentinel death → question → keep →
    ONE fresh attempt on the same tree → review."""
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_sentinel_with_pulse, _ok], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    asked = await _wait_for_question(store, d.delegation_id)
    assert "Re-run once" in asked["question"]
    await store.answer(delegation_id=d.delegation_id, response="Keep waiting")
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "review"
    assert terminal.output == "recovered output"
    assert len(runtime.calls) == 2
    names = [e.get("event") for e in _trace_events(tmp_path, d.delegation_id)]
    assert "turn_soft_keep_rerun" in names
    assert names.count("turn_soft_limit_asked") == 1


@pytest.mark.asyncio
async def test_pulsed_wire_death_stop_stops(tmp_path: Path):
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_sentinel_with_pulse, _ok], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    await _wait_for_question(store, d.delegation_id)
    await store.answer(delegation_id=d.delegation_id, response="Stop it")
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert terminal.error == "turn_stopped_by_user"
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_second_death_fails_without_second_question(tmp_path: Path):
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_sentinel_with_pulse], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    await _wait_for_question(store, d.delegation_id)
    await store.answer(delegation_id=d.delegation_id, response="Keep waiting")
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert len(runtime.calls) == 2
    names = [e.get("event") for e in _trace_events(tmp_path, d.delegation_id)]
    assert names.count("turn_soft_limit_asked") == 1


@pytest.mark.asyncio
async def test_silent_wire_death_fails_straight(tmp_path: Path):
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_sentinel_silent], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert terminal.error == SENTINEL
    assert len(runtime.calls) == 1
    rec = await store.get(delegation_id=d.delegation_id)
    assert rec is None
    names = [e.get("event") for e in _trace_events(tmp_path, d.delegation_id)]
    assert "turn_soft_limit_asked" not in names


@pytest.mark.asyncio
async def test_no_store_fails_straight(tmp_path: Path):
    runtime = _ScriptedRuntime([_sentinel_with_pulse], esc_store=None)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_existing_question_blocks_second_filing(tmp_path: Path):
    store = _esc_store(tmp_path)
    runtime = _ScriptedRuntime([_sentinel_with_pulse, _ok], esc_store=store)
    runner = _runner(tmp_path, runtime)
    d = await runner.submit(agent="backend", task="t", project_name="p1")

    first = await _wait_for_question(store, d.delegation_id)
    # Answer stop: straight fail, single question, single attempt.
    await store.answer(delegation_id=d.delegation_id, response="Stop it")
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status == "failed"
    assert first["question"]  # filed exactly once (get returns the one)
    assert len(runtime.calls) == 1


def test_last_pulse_unit(tmp_path: Path):
    from datetime import datetime, timedelta

    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog("pulse-unit", base_dir=tmp_path / "traces")
    trace.append("status_changed", {"status": "running"})
    trace.append("tool.completed", {"tool": "bash"})
    trace.close()
    from sweave.runtime.job_runner import JobRunner

    runner = _runner(tmp_path, _ScriptedRuntime([_ok]))
    pulsed = runner._last_pulse(trace)
    assert pulsed is not None
    age, desc = pulsed
    assert age < 60
    assert "bash" in desc


def test_last_pulse_none_without_pulses(tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    from sweave.runtime.job_runner import JobRunner

    trace = TraceLog("pulse-none", base_dir=tmp_path / "traces")
    trace.append("status_changed", {"status": "running"})
    trace.close()
    runner = _runner(tmp_path, _ScriptedRuntime([_ok]))
    assert runner._last_pulse(trace) is None


def test_last_pulse_none_on_missing_trace(tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    from sweave.runtime.job_runner import JobRunner

    trace = TraceLog("pulse-ghost", base_dir=tmp_path / "traces")
    runner = _runner(tmp_path, _ScriptedRuntime([_ok]))
    assert runner._last_pulse(trace) is None


def test_last_pulse_reports_old_age(tmp_path: Path):
    """Ancient pulses report honestly (the gate compares the age)."""
    from datetime import datetime, timedelta

    from sweave.runtime.job_runner import JobRunner

    traces = tmp_path / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    (traces / "pulse-old.jsonl").write_text(
        json.dumps({"event": "tool.completed", "ts": old}) + "\n",
        encoding="utf-8",
    )
    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog("pulse-old", base_dir=traces)
    runner = _runner(tmp_path, _ScriptedRuntime([_ok]))
    pulsed = runner._last_pulse(trace)
    assert pulsed is not None and pulsed[0] > 300
