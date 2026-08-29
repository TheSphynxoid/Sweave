"""Delegation + DelegationStore tests."""

from __future__ import annotations

import pytest

from sweave.runtime.delegation_store import (
    SCHEMA_VERSION,
    Delegation,
    DelegationStore,
)


def test_delegation_default_status_is_queued():
    d = Delegation(agent="backend", task="x")
    assert d.status == "queued"
    assert d.schema_version == SCHEMA_VERSION


def test_delegation_round_trip_through_dict():
    d = Delegation(agent="backend", task="x", model="m")
    d2 = Delegation.from_dict(d.to_dict())
    assert d2.agent == "backend"
    assert d2.task == "x"
    assert d2.model == "m"
    assert d2.status == "queued"


def test_delegation_from_dict_ignores_unknown_fields():
    d = Delegation.from_dict({
        "agent": "a",
        "task": "t",
        "future_field": "ignored",
        "another_one": 42,
    })
    assert d.agent == "a"
    assert d.task == "t"


def test_delegation_from_dict_handles_missing_optionals():
    d = Delegation.from_dict({"agent": "a", "task": "t"})
    assert d.status == "queued"
    assert d.error is None
    assert d.output == ""
    assert d.started_at is None
    assert d.completed_at is None


@pytest.mark.asyncio
async def test_store_add_get_list():
    store = DelegationStore()
    d1 = Delegation(agent="a", task="t1")
    d2 = Delegation(agent="b", task="t2")
    await store.add(d1)
    await store.add(d2)
    assert store.get(d1.delegation_id) is d1
    assert store.get(d2.delegation_id) is d2
    assert {d.delegation_id for d in store.list()} == {d1.delegation_id, d2.delegation_id}


@pytest.mark.asyncio
async def test_store_update_changes_fields():
    store = DelegationStore()
    d = Delegation(agent="a", task="t")
    await store.add(d)
    out = await store.update(d.delegation_id, status="running", started_at=d.created_at)
    assert out is not None
    assert out.status == "running"
    assert store.get(d.delegation_id).status == "running"


@pytest.mark.asyncio
async def test_store_update_rejects_invalid_status():
    store = DelegationStore()
    d = Delegation(agent="a", task="t")
    await store.add(d)
    with pytest.raises(ValueError):
        await store.update(d.delegation_id, status="bogus")


@pytest.mark.asyncio
async def test_store_update_missing_returns_none():
    store = DelegationStore()
    out = await store.update("does-not-exist", status="running")
    assert out is None
