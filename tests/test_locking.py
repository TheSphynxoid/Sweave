"""Per-project lock registry tests."""

from __future__ import annotations

import asyncio

import pytest

from sweave.runtime.locking import ProjectLockRegistry


@pytest.mark.asyncio
async def test_lock_for_creates_lazily():
    reg = ProjectLockRegistry()
    assert reg.known_projects() == []
    a = await reg.lock_for("p1")
    assert reg.known_projects() == ["p1"]
    # Second call returns the same lock
    a2 = await reg.lock_for("p1")
    assert a is a2


@pytest.mark.asyncio
async def test_different_projects_get_different_locks():
    reg = ProjectLockRegistry()
    a = await reg.lock_for("p1")
    b = await reg.lock_for("p2")
    assert a is not b


@pytest.mark.asyncio
async def test_same_project_serialises_concurrent_holders():
    reg = ProjectLockRegistry()
    order: list[str] = []

    async def worker(name: str, hold: float) -> None:
        lock = await reg.lock_for("p1")
        async with lock:
            order.append(f"{name}-start")
            await asyncio.sleep(hold)
            order.append(f"{name}-end")

    # Two workers; the one that wins should run to completion before
    # the other starts (because they share a lock).
    await asyncio.gather(worker("A", 0.05), worker("B", 0.01))
    # The two sequences must not interleave
    if order[0] == "A-start":
        assert order == ["A-start", "A-end", "B-start", "B-end"], order
    else:
        assert order == ["B-start", "B-end", "A-start", "A-end"], order


@pytest.mark.asyncio
async def test_concurrent_first_callers_dont_race():
    reg = ProjectLockRegistry()
    # Hammer the same project name from many tasks; they should all get
    # the same lock object.
    results = await asyncio.gather(*(reg.lock_for("p1") for _ in range(20)))
    assert all(r is results[0] for r in results), "all callers should see the same lock"
