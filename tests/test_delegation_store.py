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
    M1.9 step 3 added the v5 field (needs_attention) for the ask_human
    escalation lane in the Children tab.
    M2.0 added the v7 field (estimate) for caller-supplied
    {tokens, seconds} (record only).
    M2.1 added the v8 fields (blocking + review_request) for the
    wait-set flag + review-request record.
    M2.1-follow-up added the v9 field (engine_session_id) for the
    engine session that ran the delegation.
        Review deepening Phase 1 added the v10 field (review_bundle)
        for the transition-time diff artifact pointer.
        Per-specialist worktree policy added the v11 field
        (worktree_owned) for tree-ownership at settle.
        M2.2 added the v12 field (verdict) for reviewer judgments.
        M2.2 follow-up added the v13 fields (fix_of + fix_round)
        for fix-round lineage.
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
            # M1.9 step 3 addition
        "needs_attention",
        # M1.13 cleanup addition (ruling 2026-09-11): archive sub-state
        "archived",
        "archived_at",
        # M2.0 addition: caller-supplied estimate (record only)
        "estimate",
        # M2.1 additions: wait-set flag + review-request record
        "blocking",
        "review_request",
        # M2.1-follow-up addition: engine session that ran the delegation
        "engine_session_id",
            # Review deepening Phase 1 addition: transition-time diff pointer
            "review_bundle",
            # Worktree-policy addition: tree ownership at settle
            "worktree_owned",
            # M2.2 addition: reviewer verdict (advisory)
            "verdict",
            # M2.2 follow-up additions: fix-round lineage
            "fix_of",
            "fix_round",
        }
    actual = set(Delegation.__dataclass_fields__)  # type: ignore[attr-defined]
    assert actual == expected, (
        f"unexpected field diff: added={actual-expected}, "
        f"removed={expected-actual}"
    )


def test_schema_version_is_v11():
    """M2.2 follow-up (fix rounds): the current schema is v13
    (fix_of + fix_round lineage on top of v12's verdict record)."""
    from sweave.runtime.delegation_store import SCHEMA_VERSION

    assert SCHEMA_VERSION == 13


def test_v5_record_loads_as_v6_with_archive_defaults():
    """A v5 record (pre-M1.13) has no ``archived`` / ``archived_at``
    fields; they default to False / None."""
    from sweave.runtime.delegation_store import SCHEMA_VERSION

    v5_record = {
        "schema_version": 5,
        "delegation_id": "del-v5",
        "task_id": "t5",
        "agent": "backend",
        "model": "",
        "task": "x",
        "status": "done",
        "created_at": "2026-09-10T00:00:00",
        "updated_at": "2026-09-10T00:00:00",
        "completed_at": "2026-09-10T00:00:01",
        "kind": "task",
        "needs_attention": False,
    }
    d = Delegation.from_dict(v5_record)
    assert d.schema_version == SCHEMA_VERSION
    assert d.archived is False
    assert d.archived_at is None


def test_archive_fields_roundtrip():
    d = Delegation(agent="a", task="t")
    assert d.archived is False
    d.archived = True
    ts = datetime(2026, 9, 11, 12, 0, 0)
    d.archived_at = ts
    d2 = Delegation.from_dict(d.to_dict())
    assert d2.archived is True
    assert d2.archived_at == ts


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

# --- M1.13 cleanup (ruling 2026-09-11): archive sub-state ----------------


@pytest.mark.asyncio
async def test_store_archive_many_marks_and_persists(tmp_path):
    store = DelegationStore(tmp_path)
    d1 = Delegation(agent="a", task="t1", status="done")
    d2 = Delegation(agent="b", task="t2", status="failed")
    d3 = Delegation(agent="c", task="t3", status="done")
    await store.add(d1)
    await store.add(d2)
    await store.add(d3)
    n = await store.archive_many([d1.delegation_id, d3.delegation_id])
    assert n == 2
    assert d1.archived is True and d1.archived_at is not None
    assert d3.archived is True
    assert d2.archived is False

    # Idempotent: re-archiving changes nothing.
    assert await store.archive_many([d1.delegation_id]) == 0

    # Persisted: a fresh store instance sees the archive sub-state
    # (status + stats preserved, not deleted).
    reloaded = DelegationStore(tmp_path)
    got = reloaded.get(d1.delegation_id)
    assert got is not None
    assert got.status == "done"
    assert got.archived is True
    assert got.archived_at is not None
