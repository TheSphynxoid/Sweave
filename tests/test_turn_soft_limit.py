"""Soft total limit (incident 2026-09-11, slice 3).

The turn budget was a hard kill: an unwitnessed cap expiry failed the
delegation even when the human would rather keep waiting. Now the first
unwitnessed expiry files a blocking keep/stop question (``kind`` =
``question``, existing inline card) instead of failing:
* keep -> exactly one full re-arm, then the next unwitnessed expiry fails;
* stop / skip -> the turn fails now with ``turn_stopped_by_user``;
* a pending non-soft hold keeps the old hold path (no soft question);
* no store -> old fail-fast (best-effort).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
from sweave.runtime.escalation import EscalationStore
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.trace_log import TraceLog, read_trace


class _StubTool:
    async def execute(self, agent, task, model=None, task_id=None):
        raise AssertionError("direct _bounded_turn tests never touch the tool")


def _runner(tmp_path: Path, store, timeout: float = 0.2) -> JobRunner:
    rt = SimpleNamespace(escalation_store=store)
    return JobRunner(
        delegate_tool=_StubTool(),
        delegation_stores=PerProjectDelegationStores(),
        specialist_runtime=rt,  # type: ignore[arg-type]
        turn_timeout=timeout,
    )


def _delegation() -> Delegation:
    return Delegation(agent="backend", task="slow work", model="")


def _trace(tmp_path: Path, did: str = "d-soft") -> TraceLog:
    return TraceLog(did, base_dir=tmp_path)


def _events(tmp_path: Path, did: str = "d-soft") -> list[str]:
    return [e["event"] for e in read_trace(did, base_dir=tmp_path)]


async def _wait_soft_question(store, did: str) -> dict[str, Any]:
    for _ in range(500):
        await asyncio.sleep(0.01)
        rec = await store.get(delegation_id=did)
        if (
            rec
            and rec.get("status") == "pending"
            and (rec.get("metadata") or {}).get("soft_limit")
        ):
            return rec
    raise AssertionError("soft-limit question never filed")


@pytest.mark.asyncio
async def test_keep_arms_once_then_second_expiry_fails(tmp_path: Path):
    """Keep -> one full re-arm; the next unwitnessed expiry fails loud
    (never asks twice in one turn)."""
    store = EscalationStore(base_dir=tmp_path, timeout_seconds=None)
    runner = _runner(tmp_path, store)
    d = _delegation()
    done = asyncio.Event()

    async def work():
        await done.wait()
        return "SENTINEL"

    async def driver():
        rec = await _wait_soft_question(store, d.delegation_id)
        first_id = rec["escalation_id"]
        await store.answer(delegation_id=d.delegation_id, response="Keep waiting")
        return first_id

    main = asyncio.ensure_future(runner._bounded_turn(work(), d, _trace(tmp_path)))
    first_id = await driver()
    ok, out = await main
    # The work never finishes: the keep re-arm expires once more and
    # the second unwitnessed expiry fails (no second question).
    assert (ok, out) == (False, None)
    rec = await store.get(delegation_id=d.delegation_id)
    assert rec["escalation_id"] == first_id  # asked exactly once
    events = _events(tmp_path)
    assert "turn_soft_limit_asked" in events
    assert "turn_soft_limit_extended" in events
    assert "turn_timeout" in events


@pytest.mark.asyncio
async def test_keep_then_finish_returns_success(tmp_path: Path):
    """Keep -> work finishes inside the re-armed budget -> success."""
    store = EscalationStore(base_dir=tmp_path, timeout_seconds=None)
    runner = _runner(tmp_path, store)
    d = _delegation()
    done = asyncio.Event()

    async def work():
        await done.wait()
        return "SENTINEL"

    async def driver():
        await _wait_soft_question(store, d.delegation_id)
        await store.answer(delegation_id=d.delegation_id, response="Keep waiting")
        # Finish inside the re-armed budget: wait for the extension
        # event, then complete immediately (well before it expires).
        for _ in range(500):
            await asyncio.sleep(0.01)
            if "turn_soft_limit_extended" in _events(tmp_path):
                break
        done.set()

    main = asyncio.ensure_future(runner._bounded_turn(work(), d, _trace(tmp_path)))
    await driver()
    ok, out = await main
    # The keep re-arm fired and the work finished inside it -> success.
    assert (ok, out) == (True, "SENTINEL")
    assert "turn_soft_limit_extended" in _events(tmp_path)


@pytest.mark.asyncio
async def test_stop_fails_now_with_user_stopped(tmp_path: Path):
    """'Stop it' fails the turn immediately with the user_stopped detail."""
    store = EscalationStore(base_dir=tmp_path, timeout_seconds=None)
    runner = _runner(tmp_path, store)
    d = _delegation()

    async def work():
        await asyncio.sleep(1000)
        return "NEVER"

    async def driver():
        await _wait_soft_question(store, d.delegation_id)
        await store.answer(delegation_id=d.delegation_id, response="Stop it")

    main = asyncio.ensure_future(runner._bounded_turn(work(), d, _trace(tmp_path)))
    await driver()
    ok, out = await main
    assert (ok, out) == (False, "user_stopped")
    events = _events(tmp_path)
    assert "turn_soft_limit_stop" in events
    assert "turn_timeout" not in events


@pytest.mark.asyncio
async def test_non_soft_hold_takes_old_path_no_soft_question(tmp_path: Path):
    """A pending permission hold keeps the existing hold path; no soft
    question is filed alongside it."""
    store = EscalationStore(base_dir=tmp_path, timeout_seconds=None)
    runner = _runner(tmp_path, store)
    d = _delegation()
    done = asyncio.Event()

    await store.create(
        delegation_id=d.delegation_id,
        question="Permission required: ...",
        options=["allow once", "deny"],
        kind="permission",
        audience="human",
        timeout_seconds=None,
        metadata={"requestID": "per_x"},
    )

    async def work():
        await done.wait()
        return "SENTINEL"

    async def driver():
        # Wait for the hold to engage (event-gated, not sleep-gated),
        # answer it, then finish: the turn must succeed with no soft
        # question ever filed.
        for _ in range(500):
            await asyncio.sleep(0.01)
            if any(
                e.get("event") == "turn_extended"
                for e in read_trace("d-soft", base_dir=tmp_path)
            ):
                break
        await store.answer(delegation_id=d.delegation_id, response="allow once")
        done.set()

    main = asyncio.ensure_future(runner._bounded_turn(work(), d, _trace(tmp_path)))
    await driver()
    ok, out = await main
    assert (ok, out) == (True, "SENTINEL")
    events = _events(tmp_path)
    assert "turn_soft_limit_asked" not in events
    assert any(
        e.get("event") == "turn_extended" and e.get("reason") == "escalation_pending"
        for e in read_trace("d-soft", base_dir=tmp_path)
    )


@pytest.mark.asyncio
async def test_no_store_fails_fast_as_before(tmp_path: Path):
    """No runtime/store wired: old fail-fast, no question possible."""
    runner = JobRunner(
        delegate_tool=_StubTool(),
        delegation_stores=PerProjectDelegationStores(),
        specialist_runtime=None,
        turn_timeout=0.2,
    )
    d = _delegation()

    async def work():
        await asyncio.sleep(1000)
        return "NEVER"

    ok, out = await runner._bounded_turn(work(), d, _trace(tmp_path))
    assert (ok, out) == (False, None)
    assert "turn_timeout" in _events(tmp_path)
