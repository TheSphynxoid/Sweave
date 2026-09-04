"""Delegation + DelegationStore tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from sweave.runtime.delegation_store import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_PREP,
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
async def test_store_add_get_list(tmp_path):
    store = DelegationStore(tmp_path)
    d1 = Delegation(agent="a", task="t1")
    d2 = Delegation(agent="b", task="t2")
    await store.add(d1)
    await store.add(d2)
    assert store.get(d1.delegation_id) is d1
    assert store.get(d2.delegation_id) is d2
    assert {d.delegation_id for d in store.list()} == {d1.delegation_id, d2.delegation_id}


@pytest.mark.asyncio
async def test_store_update_changes_fields(tmp_path):
    store = DelegationStore(tmp_path)
    d = Delegation(agent="a", task="t")
    await store.add(d)
    out = await store.update(d.delegation_id, status="running", started_at=d.created_at)
    assert out is not None
    assert out.status == "running"
    assert store.get(d.delegation_id).status == "running"


@pytest.mark.asyncio
async def test_store_update_rejects_invalid_status(tmp_path):
    store = DelegationStore(tmp_path)
    d = Delegation(agent="a", task="t")
    await store.add(d)
    with pytest.raises(ValueError):
        await store.update(d.delegation_id, status="bogus")


@pytest.mark.asyncio
async def test_store_update_missing_returns_none(tmp_path):
    store = DelegationStore(tmp_path)
    out = await store.update("does-not-exist", status="running")
    assert out is None


# --- M1.1 step 1: Delegation v2 fields + v1->v2 migration ----------------


def test_schema_version_is_v2_or_higher():
    """M1.1 step 1 bumped to v2; M1.6 step 2 bumped to v3. The
    migration chain in ``Delegation.from_dict`` covers both; this
    test pins the constants and the version ordering so a future
    bump doesn't accidentally break the migration chain."""
    assert SCHEMA_VERSION >= 3
    assert SCHEMA_VERSION_PREP == 1
    assert SCHEMA_VERSION > SCHEMA_VERSION_PREP


def test_v2_fields_have_none_defaults():
    d = Delegation(agent="backend", task="x")
    assert d.worktree_path is None
    assert d.branch is None
    assert d.pr_url is None
    assert d.parent_task_id is None
    assert d.manifest is None
    assert d.schema_version == SCHEMA_VERSION


def test_v2_round_trip_with_all_new_fields():
    d = Delegation(
        agent="backend",
        task="x",
        worktree_path="/tmp/wt",
        branch="sweave/abc-backend",
        pr_url="https://github.com/x/y/pull/1",
        parent_task_id="parent_abc",
        manifest={
            "files_touched": ["src/api.py", "tests/test_api.py"],
            "intent": "Add /api/users endpoint",
            "confidence": 0.92,
            "breaking_change": False,
        },
    )
    d2 = Delegation.from_dict(d.to_dict())
    assert d2.worktree_path == "/tmp/wt"
    assert d2.branch == "sweave/abc-backend"
    assert d2.pr_url == "https://github.com/x/y/pull/1"
    assert d2.parent_task_id == "parent_abc"
    assert d2.manifest is not None
    assert d2.manifest["files_touched"] == ["src/api.py", "tests/test_api.py"]
    assert d2.manifest["intent"] == "Add /api/users endpoint"
    assert d2.manifest["confidence"] == 0.92
    assert d2.manifest["breaking_change"] is False


def test_v1_record_loads_as_v2_with_none_fields():
    """A prep-era v1 record (no v2 fields) loads as v2 with all v2 fields None."""
    v1_dict = {
        "schema_version": 1,
        "delegation_id": "d_legacy",
        "task_id": "t_legacy",
        "agent": "backend",
        "model": "ollama/qwen3:8b",
        "task": "legacy task",
        "status": "done",
        "created_at": "2026-08-29T10:00:00",
        "updated_at": "2026-08-29T10:05:00",
        "started_at": "2026-08-29T10:00:30",
        "completed_at": "2026-08-29T10:05:00",
        "parent_session_id": "sess_1",
        "project_name": "sweave",
        "output": "legacy output",
        "error": None,
    }
    d = Delegation.from_dict(v1_dict)
    assert d.schema_version == SCHEMA_VERSION
    assert d.delegation_id == "d_legacy"
    assert d.status == "done"
    assert d.output == "legacy output"
    # v2 fields are None (we don't fabricate; v1 didn't carry them).
    assert d.worktree_path is None
    assert d.branch is None
    assert d.pr_url is None
    assert d.parent_task_id is None
    assert d.manifest is None
    # Datetimes parsed correctly
    assert isinstance(d.created_at, datetime)
    assert d.completed_at == datetime.fromisoformat("2026-08-29T10:05:00")


def test_v1_record_without_schema_version_loads():
    """Very old v1 records might lack schema_version entirely; default to v1."""
    v1_dict = {
        "delegation_id": "d_anon",
        "agent": "backend",
        "task": "t",
        "status": "queued",
    }
    d = Delegation.from_dict(v1_dict)
    assert d.schema_version == SCHEMA_VERSION
    assert d.worktree_path is None


def test_unknown_fields_still_dropped():
    """Future-schema fields are dropped silently (forward compat at the field level)."""
    d = Delegation.from_dict({
        "agent": "a",
        "task": "t",
        "v3_field": "ignored",
        "another_v3": 42,
    })
    assert d.agent == "a"
    assert d.task == "t"
    assert d.schema_version == SCHEMA_VERSION


def test_manifest_partial_is_preserved():
    """Manifest is a TypedDict(total=False) — only the fields present survive."""
    d = Delegation(
        agent="a", task="t",
        manifest={"intent": "minimal"},  # only one field
    )
    d2 = Delegation.from_dict(d.to_dict())
    assert d2.manifest is not None
    assert d2.manifest["intent"] == "minimal"
    # Other fields absent from source are absent on the roundtrip too.
    assert "files_touched" not in d2.manifest
    assert "confidence" not in d2.manifest
    assert "breaking_change" not in d2.manifest


def test_manifest_can_be_omitted_via_to_dict_roundtrip():
    d = Delegation(agent="a", task="t", manifest={"intent": "x"})
    d.manifest = None  # external code can clear
    d2 = Delegation.from_dict(d.to_dict())
    assert d2.manifest is None


def test_to_dict_serialises_datetimes():
    d = Delegation(agent="a", task="t")
    data = d.to_dict()
    assert isinstance(data["created_at"], str)
    assert isinstance(data["updated_at"], str)
    assert data["started_at"] is None
    assert data["completed_at"] is None


def test_v3_field_set_includes_all_m1_1_plus_m1_6_fields():
    """Lock the public field surface -- adding a field requires bumping SCHEMA_VERSION.

    M1.1 step 1 added the v2 fields (worktree/branch/pr_url/parent_task_id/manifest).
    M1.6 step 2 added the v3 fields (depth/chain_root_id/coordination_tokens) for
    the deferral chain (orchestrator -> specialist -> defer -> ...).
    M1.7 step 2 added the v4 field (kind) for the chat vs task distinction.
    """
    expected = {
        "schema_version", "delegation_id", "task_id", "agent", "model", "task",
        "status", "created_at", "updated_at", "started_at", "completed_at",
        "parent_session_id", "project_name", "output", "error",
        # M1.1 step 1 additions
        "worktree_path", "branch", "pr_url", "parent_task_id", "manifest",
        # M1.6 step 2 additions
        "depth", "chain_root_id", "coordination_tokens",
        # M1.7 step 2 addition
        "kind",
    }
    actual = set(Delegation.__dataclass_fields__)  # type: ignore[attr-defined]
    assert actual == expected, (
        f"unexpected field diff: added={actual-expected}, "
        f"removed={expected-actual}"
    )


def test_schema_version_is_v4():
    """M1.7 step 2: the current schema is v4 (kind was added on top of v3)."""
    from sweave.runtime.delegation_store import SCHEMA_VERSION

    assert SCHEMA_VERSION == 4


def test_v2_to_v3_migration_fills_defaults():
    """A v2 record (pre-M1.6) loads with depth=0, chain_root_id=None,
    coordination_tokens=0. M1.6 never persisted depth>0 chains before
    the field existed, so defaults are correct."""
    from sweave.runtime.delegation_store import SCHEMA_VERSION

    v2_record = {
        "schema_version": 2,
        "delegation_id": "del-abc",
        "task_id": "t1",
        "agent": "backend",
        "model": "",
        "task": "x",
        "status": "done",
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
        "started_at": "2026-09-01T00:00:00",
        "completed_at": "2026-09-01T00:00:01",
        "parent_session_id": None,
        "project_name": None,
        "output": "",
        "error": None,
        "worktree_path": None,
        "branch": None,
        "pr_url": None,
        "parent_task_id": None,
        "manifest": None,
    }
    d = Delegation.from_dict(v2_record)
    assert d.schema_version == SCHEMA_VERSION
    assert d.depth == 0
    assert d.chain_root_id is None
    assert d.coordination_tokens == 0


def test_v1_to_v3_migration_also_works():
    """M1.prep records (v1) load through both migration helpers and
    round-trip as v3."""
    from sweave.runtime.delegation_store import SCHEMA_VERSION

    v1_record = {
        "schema_version": 1,
        "delegation_id": "del-prep",
        "task_id": "t0",
        "agent": "backend",
        "model": "",
        "task": "x",
        "status": "done",
        "created_at": "2026-08-01T00:00:00",
        "updated_at": "2026-08-01T00:00:00",
        "started_at": None,
        "completed_at": None,
        "parent_session_id": None,
        "project_name": None,
        "output": "",
        "error": None,
    }
    d = Delegation.from_dict(v1_record)
    assert d.schema_version == SCHEMA_VERSION
    assert d.depth == 0
    assert d.parent_task_id is None  # v1 didn't have this; defaults
    assert d.coordination_tokens == 0
