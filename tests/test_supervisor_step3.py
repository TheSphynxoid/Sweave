"""Supervisor step 3: pulse supervision (engine-first).

A turn is killed for pulselessness verified against aliveness,
never for age alone. Pins (all fast: windows shrunk, never the
production values):
* pulsing turns never trip at any age (the core promise);
* pulseless + no asker trips at the fuse with a named reason;
* certain death (dead serve) trips fast with a handoff event;
* uncertain silence without an asker waits with progress;
* the serve/sidecar aliveness helpers degrade to unknown, never
  death, on anything unresolvable.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sweave.runtime import job_runner as jr_module
from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.trace_log import TraceLog


def _runner(**kw) -> JobRunner:
    args: dict[str, Any] = dict(
        delegate_tool=None,  # type: ignore[arg-type]
        delegation_stores=PerProjectDelegationStores(),
        turn_timeout=30.0,
    )
    args.update(kw)
    return JobRunner(**args)  # type: ignore[arg-type]


def _delegation(**kw):
    from sweave.runtime.delegation_store import Delegation

    base = dict(agent="backend", task="t", project_name="p")
    base.update(kw)
    return Delegation(**base)


def _events(tmp_path: Path, delegation_id: str) -> list[dict]:
    path = TraceLog(delegation_id, base_dir=tmp_path / "traces").path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_pulsing_turn_never_trips(tmp_path: Path, monkeypatch):
    """The core promise: steady pulses beat any fuse (old code
    failed this turn at the first budget expiry with no store)."""
    monkeypatch.setattr(jr_module, "SUPERVISOR_SLICE_SECONDS", 0.5)
    monkeypatch.setattr(jr_module, "QUIET_WINDOW_SECONDS", 2.0)
    runner = _runner(turn_timeout=2.0)
    d = _delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")

    async def pulsing_work():
        for _ in range(10):
            trace.append("tool.completed", {"tool": "bash"})
            await asyncio.sleep(0.5)
        return "steady work done"

    ok, output = await runner._bounded_turn(pulsing_work(), d, trace)
    trace.close()
    assert (ok, output) == (True, "steady work done")
    names = [e.get("event") for e in _events(tmp_path, d.delegation_id)]
    assert "turn_timeout" not in names
    assert "turn_soft_limit_asked" not in names
    assert "turn_no_progress" not in names


@pytest.mark.asyncio
async def test_pulseless_no_asker_trips_at_fuse(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(jr_module, "SUPERVISOR_SLICE_SECONDS", 0.5)
    monkeypatch.setattr(jr_module, "QUIET_WINDOW_SECONDS", 2.0)
    runner = _runner(turn_timeout=2.0)
    d = _delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")

    async def silent():
        await asyncio.sleep(30)
        return "never"

    ok, output = await runner._bounded_turn(silent(), d, trace)
    trace.close()
    assert (ok, output) == (False, None)
    names = [e.get("event") for e in _events(tmp_path, d.delegation_id)]
    assert "turn_timeout" in names


@pytest.mark.asyncio
async def test_dead_serve_trips_fast_with_handoff(tmp_path: Path, monkeypatch):
    """Certain death (dead serve) trips at the first quiet window —
    no waiting for the fuse."""
    monkeypatch.setattr(jr_module, "SUPERVISOR_SLICE_SECONDS", 0.5)
    monkeypatch.setattr(jr_module, "QUIET_WINDOW_SECONDS", 2.0)

    class _DeadRunner:
        def is_alive(self) -> bool:
            return False

    class _Registry:
        def peek(self, name, path):
            return _DeadRunner()

    runner = _runner(
        turn_timeout=300.0,
        specialist_runtime=SimpleNamespace(runners=_Registry()),  # type: ignore[arg-type]
    )
    d = _delegation(worktree_path=str(tmp_path))
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")

    async def silent():
        await asyncio.sleep(30)
        return "never"

    ok, output = await runner._bounded_turn(silent(), d, trace)
    trace.close()
    assert (ok, output) == (False, None)
    events = _events(tmp_path, d.delegation_id)
    trips = [e for e in events if e.get("event") == "turn_no_progress"]
    assert len(trips) == 1
    assert trips[0]["reason"] == "serve_dead"


@pytest.mark.asyncio
async def test_uncertain_silence_waits_with_progress(tmp_path: Path, monkeypatch):
    """No asker + fuse far away: progress trace + rolled window,
    no trip, no question."""
    monkeypatch.setattr(jr_module, "SUPERVISOR_SLICE_SECONDS", 0.5)
    monkeypatch.setattr(jr_module, "QUIET_WINDOW_SECONDS", 2.0)
    monkeypatch.setattr(jr_module, "SOFT_VERDICT_POLL_SECONDS", 0.01)
    runner = _runner(turn_timeout=300.0)
    d = _delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")

    async def silent_then_done():
        await asyncio.sleep(4)
        return "late but alive"

    ok, output = await runner._bounded_turn(silent_then_done(), d, trace)
    trace.close()
    assert (ok, output) == (True, "late but alive")
    events = _events(tmp_path, d.delegation_id)
    waits = [e for e in events if e.get("event") == "waiting_with_progress"]
    assert len(waits) >= 1
    assert waits[0]["last_pulse"] == "no pulses yet"
    assert "turn_timeout" not in [e.get("event") for e in events]


def test_sidecar_alive_degrades(monkeypatch):
    import sweave.harness.engine as eng

    monkeypatch.setattr(eng, "_sidecar", None)
    assert eng.sidecar_alive() is None

    class _Proc:
        def __init__(self, rc):
            self.returncode = rc

    monkeypatch.setattr(eng, "_sidecar", SimpleNamespace(process=_Proc(None)))
    assert eng.sidecar_alive() is True
    monkeypatch.setattr(eng, "_sidecar", SimpleNamespace(process=_Proc(1)))
    assert eng.sidecar_alive() is False
    monkeypatch.setattr(eng, "_sidecar", SimpleNamespace(process=object()))
    assert eng.sidecar_alive() is None


def test_serve_alive_for_unknowns(tmp_path: Path):
    runner = _runner()
    d = _delegation()
    # No runtime at all.
    assert runner._serve_alive_for(d) is None
    # Runtime without runners (legacy/odd doubles).
    runner2 = _runner(specialist_runtime=SimpleNamespace())  # type: ignore[arg-type]
    assert runner2._serve_alive_for(d) is None

    class _DeadRunner:
        def is_alive(self) -> bool:
            return False

    class _LiveRunner:
        def is_alive(self) -> bool:
            return True

    class _Registry:
        def __init__(self, runner):
            self._runner = runner

        def peek(self, name, path):
            # Like the real peek: unknown keys miss (None), only
            # the known tree resolves to the runner.
            if "nonexistent" in str(path):
                return None
            return self._runner

    d2 = _delegation(worktree_path=str(tmp_path))
    r_dead = _runner(
        specialist_runtime=SimpleNamespace(runners=_Registry(_DeadRunner()))  # type: ignore[arg-type]
    )
    assert r_dead._serve_alive_for(d2) is False
    r_live = _runner(
        specialist_runtime=SimpleNamespace(runners=_Registry(_LiveRunner()))  # type: ignore[arg-type]
    )
    assert r_live._serve_alive_for(d2) is True
    # Unresolvable tree path degrades (never death).
    d3 = _delegation(worktree_path="/nonexistent/\0 Garland")
    assert r_dead._serve_alive_for(d3) is None
