"""Tests for SubAgentRunStore (M1.1 step 3).

The store is per-process, in-memory only, FIFO-capped at MAX_RUNS.
M1.1 only delivers the type + lifecycle primitives; the API
endpoints arrive in step 4.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sweave.runtime.subagent_store import (
    MAX_RUNS,
    SubAgentRun,
    SubAgentRunStore,
)


# ---- Construction & basic lifecycle -------------------------------------


def test_default_cap_is_max_runs():
    assert SubAgentRunStore().max_runs == MAX_RUNS


def test_rejects_zero_or_negative_cap():
    with pytest.raises(ValueError):
        SubAgentRunStore(max_runs=0)
    with pytest.raises(ValueError):
        SubAgentRunStore(max_runs=-1)


def test_new_store_is_empty():
    store = SubAgentRunStore()
    assert store.size == 0
    assert store.list() == []
    assert store.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_add_and_get_round_trip():
    store = SubAgentRunStore()
    r = SubAgentRun(agent="investigator", purpose="explore")
    await store.add(r)
    assert store.size == 1
    assert store.get(r.run_id) is r


@pytest.mark.asyncio
async def test_list_returns_newest_first():
    store = SubAgentRunStore()
    older = SubAgentRun(agent="a", started_at=datetime(2026, 1, 1))
    newer = SubAgentRun(agent="b", started_at=datetime(2026, 1, 2))
    await store.add(older)
    await store.add(newer)
    assert store.list() == [newer, older]


# ---- Status transitions --------------------------------------------------


@pytest.mark.asyncio
async def test_status_done_sets_finished_at_via_update():
    store = SubAgentRunStore()
    r = SubAgentRun(agent="a")
    await store.add(r)
    finished = datetime.now()
    out = await store.update(r.run_id, status="done", finished_at=finished, output_summary="ok")
    assert out is not None
    assert out.status == "done"
    assert out.finished_at == finished
    assert out.output_summary == "ok"


@pytest.mark.asyncio
async def test_status_failed():
    store = SubAgentRunStore()
    r = SubAgentRun(agent="a")
    await store.add(r)
    out = await store.update(r.run_id, status="failed", error="nope")
    # 'error' is not a SubAgentRun field; update silently drops it.
    assert out is not None
    assert out.status == "failed"


@pytest.mark.asyncio
async def test_update_rejects_invalid_status():
    store = SubAgentRunStore()
    r = SubAgentRun(agent="a")
    await store.add(r)
    with pytest.raises(ValueError):
        await store.update(r.run_id, status="bogus")


@pytest.mark.asyncio
async def test_update_missing_returns_none():
    store = SubAgentRunStore()
    out = await store.update("nope", status="done")
    assert out is None


# ---- Cap eviction (FIFO) --------------------------------------------------


@pytest.mark.asyncio
async def test_cap_drops_oldest_when_exceeded():
    store = SubAgentRunStore(max_runs=3)
    runs = [
        SubAgentRun(agent=f"a{i}", started_at=datetime(2026, 1, i + 1))
        for i in range(5)
    ]
    for r in runs:
        await store.add(r)
    # We added 5, capped at 3 -> the two oldest (i=0, i=1) are gone.
    assert store.size == 3
    assert store.get(runs[0].run_id) is None
    assert store.get(runs[1].run_id) is None
    assert store.get(runs[2].run_id) is runs[2]
    assert store.get(runs[3].run_id) is runs[3]
    assert store.get(runs[4].run_id) is runs[4]
    # Newest-first listing.
    assert [r.run_id for r in store.list()] == [
        runs[4].run_id, runs[3].run_id, runs[2].run_id,
    ]


@pytest.mark.asyncio
async def test_cap_at_exact_boundary_keeps_all():
    store = SubAgentRunStore(max_runs=3)
    for i in range(3):
        await store.add(SubAgentRun(agent=f"a{i}"))
    assert store.size == 3


# ---- Concurrency (sanity) -------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_adds_respect_cap():
    import asyncio

    store = SubAgentRunStore(max_runs=10)
    await asyncio.gather(*(store.add(SubAgentRun(agent=f"a{i}")) for i in range(50)))
    # Cap holds; no record above 10.
    assert store.size == 10


# ---- Serialisation -------------------------------------------------------


def test_to_dict_serialises_datetimes():
    r = SubAgentRun(agent="a", started_at=datetime(2026, 8, 29, 12, 0))
    d = r.to_dict()
    assert d["agent"] == "a"
    assert d["started_at"] == "2026-08-29T12:00:00"
    assert d["finished_at"] is None
    assert d["status"] == "running"


def test_to_dict_after_finish():
    r = SubAgentRun(agent="a")
    r.finished_at = datetime(2026, 8, 29, 12, 5)
    r.status = "done"
    d = r.to_dict()
    assert d["status"] == "done"
    assert d["finished_at"] == "2026-08-29T12:05:00"


# ---- Defaults -------------------------------------------------------------


def test_default_status_running():
    r = SubAgentRun(agent="a")
    assert r.status == "running"
    assert r.finished_at is None
    assert r.output_summary == ""


def test_run_id_is_unique():
    ids = {SubAgentRun().run_id for _ in range(100)}
    assert len(ids) == 100
