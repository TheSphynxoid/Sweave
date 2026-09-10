"""M1.12 turn-hold fix (2026-09-10, user ruling "full budget re-armed").

A child delegation's ``_bounded_turn`` must SUSPEND its countdown while
the delegation has a pending escalation (blocking human question, e.g.
a ``permission`` ask): every expiry re-arms a full budget for as long
as the question is open, and one final full re-arm fires after the
question resolves. Regression context: reviewer delegation
``020e3ebb8d1b`` was killed by the 900s bound with its permission
question pending — the ChatLoop suspension (M1.12 step 3) never
covered the child path.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.trace_log import TraceLog


class StubEscalationStore:
    """Answers ``pending`` for the first N ``get`` calls, then resolved."""

    def __init__(self, pending_calls: int) -> None:
        self.remaining = pending_calls
        self.calls = 0

    async def get(self, *, delegation_id: str):  # noqa: ANN001
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            return {"status": "pending"}
        return {"status": "answered"}


def _delegation() -> Delegation:
    return Delegation(
        delegation_id="d-hold",
        task_id="t-hold",
        agent="reviewer",
        model="",
        task="diagnose",
        status="running",
        project_name="p",
    )


def _runner(store: StubEscalationStore | None, turn_timeout: float) -> JobRunner:
    runtime = (
        SimpleNamespace(escalation_store=store) if store is not None else None
    )
    return JobRunner(
        delegate_tool=object(),  # never touched by _bounded_turn
        delegation_stores=PerProjectDelegationStores(),
        specialist_runtime=runtime,  # type: ignore[arg-type]
        turn_timeout=turn_timeout,
    )


def _trace_events(base_dir: Path, delegation_id: str) -> list[dict]:
    f = base_dir / f"{delegation_id}.jsonl"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]


def test_pending_escalation_holds_the_bound(tmp_path: Path) -> None:
    """A turn held by a pending question survives past its budget and
    gets a full re-arm after the question resolves.

    (Timings are >=1s because ``_bounded_turn`` clamps the window to
    ``max(budget, 1.0)``.)
    """
    store = StubEscalationStore(pending_calls=3)
    runner = _runner(store, turn_timeout=1.0)
    d = _delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path)

    async def coro():
        await asyncio.sleep(4.5)
        return "diagnosis complete"

    ok, output = asyncio.run(runner._bounded_turn(coro(), d, trace))
    assert ok is True
    assert output == "diagnosis complete"

    events = _trace_events(tmp_path, d.delegation_id)
    reasons = [
        e.get("reason") for e in events if e.get("event") == "turn_extended"
    ]
    assert reasons.count("escalation_pending") == 3
    assert reasons.count("escalation_resolved_rearm") == 1
    assert not any(e.get("event") == "turn_timeout" for e in events)


def test_no_escalation_store_fails_normally(tmp_path: Path) -> None:
    """Without a store the bound behaves exactly as before the fix."""
    runner = _runner(None, turn_timeout=1.0)
    d = _delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path)

    async def coro():
        await asyncio.sleep(2.2)
        return "too late"

    ok, output = asyncio.run(runner._bounded_turn(coro(), d, trace))
    assert ok is False
    assert output is None
    events = _trace_events(tmp_path, d.delegation_id)
    assert any(e.get("event") == "turn_timeout" for e in events)
    assert not any(e.get("event") == "turn_extended" for e in events)


def test_escalation_pending_helper_is_best_effort(tmp_path: Path) -> None:
    runner = _runner(None, turn_timeout=0.1)
    d = _delegation()
    # No runtime wired -> never held.
    assert asyncio.run(runner._escalation_pending(d)) is False

    class Boom:
        async def get(self, *, delegation_id):  # noqa: ANN001
            raise RuntimeError("store exploded")

    runner_boom = JobRunner(
        delegate_tool=object(),
        delegation_stores=PerProjectDelegationStores(),
        specialist_runtime=SimpleNamespace(escalation_store=Boom()),  # type: ignore[arg-type]
        turn_timeout=0.1,
    )
    # Store error -> not held (the bound degrades to pre-fix behaviour).
    assert asyncio.run(runner_boom._escalation_pending(d)) is False

    runner_hold = _runner(StubEscalationStore(pending_calls=1), 0.1)
    assert asyncio.run(runner_hold._escalation_pending(d)) is True


def test_answered_escalation_does_not_hold(tmp_path: Path) -> None:
    """A resolved question must not extend anything (only the one
    re-arm after a hold does)."""
    store = StubEscalationStore(pending_calls=0)  # answered from the start
    runner = _runner(store, turn_timeout=1.0)
    d = _delegation()
    trace = TraceLog(d.delegation_id, base_dir=tmp_path)

    async def coro():
        await asyncio.sleep(2.2)
        return "late"

    ok, _ = asyncio.run(runner._bounded_turn(coro(), d, trace))
    assert ok is False
    events = _trace_events(tmp_path, d.delegation_id)
    assert not any(
        e.get("reason") == "escalation_resolved_rearm" for e in events
    )
