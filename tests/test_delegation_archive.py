"""M1.13 cleanup pass — dead-scope delegation archive sweep + cascades.

User ruling 2026-09-11: ARCHIVE, not delete. Records referencing dead
projects (unregistered / workdir gone) are swept to ``archived``; on
project/session delete the delegations cascade to ``archived``; the
stats are preserved — GET /api/delegations keeps counting archived
records via the aggregate surface (``archived_projects``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sweave.runtime.delegation_archive import (
    ArchiveIndexStore,
    archive_project_scope,
    archive_session_scope,
    archived_project_aggregates,
    slugify_project_name,
    sweep_archive_delegations,
)
from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
)


def _mk_delegation(project_name, *, status="done", session=None, kind="task"):
    return Delegation(
        agent="backend",
        task=f"task for {project_name}",
        status=status,
        project_name=project_name,
        parent_session_id=session,
        kind=kind,
    )


async def _seed_store(stores, dir_path, delegations):
    store = await stores.for_project(dir_path)
    for d in delegations:
        await store.add(d)
    return store


def _record_archived(stores, delegation_id):
    """Find a record across the registry's stores; return its archived flag."""
    for store in stores.known_projects_stores():
        rec = store.get(delegation_id)
        if rec is not None:
            return rec.archived
    raise AssertionError("record not found in any store")


# ---------------------------------------------------------------------------
# (a) sweep: dead-scope archive + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_archives_unregistered_project_scopes(tmp_path):
    from sweave.projects import ProjectManager

    # tmp_home (conftest autouse) already redirected Path.home(); the
    # PM is anchored there.
    pm = ProjectManager()
    registered_dir = tmp_path / "proj_a"
    registered_dir.mkdir()
    pm.create_project("ProjA", registered_dir)

    stores = PerProjectDelegationStores()
    sweep_dir = tmp_path / "sweep_dir"
    sweep_dir.mkdir()
    live = _mk_delegation("ProjA")
    fallback = _mk_delegation(None)
    ghost1 = _mk_delegation("Ghost1", status="done")
    ghost2 = _mk_delegation("Ghost1", status="failed")
    # Seeding registers the store in the registry — exactly how the
    # live server's in-memory ghost stores are reachable post-boot.
    await _seed_store(stores, sweep_dir, [live, fallback, ghost1, ghost2])

    index = ArchiveIndexStore(base_dir=tmp_path / "archived")
    stats = await sweep_archive_delegations(
        stores, index, fallback_dir=Path(pm.base_path), project_manager=pm
    )
    assert stats["archived_records"] == 2

    target = await stores.for_project(sweep_dir)
    assert target.get(ghost1.delegation_id).archived is True
    assert target.get(ghost1.delegation_id).archived_at is not None
    assert target.get(ghost2.delegation_id).archived is True
    # Live project-scoped records are never swept.
    assert target.get(live.delegation_id).archived is False
    # Records without a project scope are never swept.
    assert target.get(fallback.delegation_id).archived is False
    # Status + stats preserved (archive, not delete).
    got = target.get(ghost1.delegation_id)
    assert got.status == "done"
    assert got.task == ghost1.task

    # Idempotent: second sweep archives nothing new.
    stats2 = await sweep_archive_delegations(
        stores, index, fallback_dir=Path(pm.base_path), project_manager=pm
    )
    assert stats2["archived_records"] == 0


@pytest.mark.asyncio
async def test_sweep_archives_registered_project_with_dead_workdir(
    tmp_path,
):
    """A project still registered whose workdir path no longer exists is
    a dead scope; its records are archived."""
    from sweave.projects import ProjectManager

    pm = ProjectManager()
    vanished = tmp_path / "vanished_dir"
    vanished.mkdir()
    pm.create_project("Gone", vanished)
    vanished.rmdir()

    stores = PerProjectDelegationStores()
    store_dir = tmp_path / "store_dir"
    store_dir.mkdir()
    gone = _mk_delegation("Gone", status="review")
    await _seed_store(stores, store_dir, [gone])

    index = ArchiveIndexStore(base_dir=tmp_path / "archived")
    stats = await sweep_archive_delegations(
        stores, index, fallback_dir=Path(pm.base_path)
    )
    assert stats["archived_records"] == 1
    assert _record_archived(stores, gone.delegation_id) is True


@pytest.mark.asyncio
async def test_sweep_after_project_removal_from_registry(tmp_path):
    from sweave.projects import ProjectManager

    pm = ProjectManager()
    dir_path = tmp_path / "d"
    dir_path.mkdir()
    pm.create_project("X", dir_path)

    stores = PerProjectDelegationStores()
    evicted = _mk_delegation("X", status="done")
    await _seed_store(stores, dir_path, [evicted])
    # Project removed from the registry (no workdir deletion — the
    # delegations.json file stays on disk with the records).
    pm.delete_project("X")

    index = ArchiveIndexStore(base_dir=tmp_path / "archived")
    stats = await sweep_archive_delegations(
        stores, index, fallback_dir=Path(pm.base_path)
    )
    assert stats["archived_records"] == 1
    assert _record_archived(stores, evicted.delegation_id) is True
    # The sweep persists the aggregate so the stats survive even after
    # the store becomes unreachable on the next boot.
    entry = index.get("X")
    assert entry is not None
    assert entry["total"] == 1
    assert entry["source"] == "index"


# ---------------------------------------------------------------------------
# Index: shape + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_entry_shape(tmp_path):
    from sweave.projects import ProjectManager

    pm = ProjectManager()
    dir_path = tmp_path / "w"
    dir_path.mkdir()
    pm.create_project("Shaped", dir_path)
    stores = PerProjectDelegationStores()
    await _seed_store(
        stores,
        dir_path,
        [
            # Ghost records for a DEAD scope ("DeadShape", unregistered)
            # living inside a live project's store file — the sweep
            # archives them and persists the aggregate row.
            _mk_delegation("DeadShape", status="done"),
            _mk_delegation("DeadShape", status="failed"),
        ],
    )
    index = ArchiveIndexStore(base_dir=tmp_path / "archived")
    stats = await sweep_archive_delegations(
        stores, index, fallback_dir=Path(pm.base_path)
    )
    entries = index.list_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["project_name"] == "DeadShape"
    assert entry["total"] == 2
    assert entry["by_status"] == {"done": 1, "failed": 1}
    assert entry["by_kind"] == {"task": 2}
    assert entry["source"] == "index"
    assert isinstance(entry["archived_at"], str)
    assert isinstance(entry["last_created_at"], str)


@pytest.mark.asyncio
async def test_index_upsert_preserves_earliest_timestamp(tmp_path):
    index = ArchiveIndexStore(base_dir=tmp_path / "archived")
    index.upsert(
        {
            "project_name": "Up",
            "workdir": None,
            "total": 3,
            "by_status": {"done": 3},
            "by_kind": {"task": 3},
            "archived_at": "2026-09-11T01:00:00",
            "last_created_at": None,
            "source": "index",
        }
    )
    # A later sweep computing the same group must not lose the
    # original archive timestamp.
    index.upsert(
        {
            "project_name": "Up",
            "workdir": None,
            "total": 3,
            "by_status": {"done": 3},
            "by_kind": {"task": 3},
            "archived_at": "2099-01-01T00:00:00",
            "last_created_at": None,
            "source": "index",
        }
    )
    got = index.get("Up")
    assert got["archived_at"] == "2026-09-11T01:00:00"
    assert index.list_entries() == [got]


def test_slugify_is_filesystem_safe():
    assert slugify_project_name("My Project/App?") == "My_Project_App"
    assert slugify_project_name("###") == "unnamed"
    assert slugify_project_name("a-b_c") == "a-b_c"


# ---------------------------------------------------------------------------
# Aggregate surface helper: archived records still counted
# ---------------------------------------------------------------------------


def test_aggregates_union_store_and_index_store_wins(tmp_path):
    index = ArchiveIndexStore(base_dir=tmp_path / "archived")
    live = [
        _mk_delegation("GhostLive", status="done"),
        _mk_delegation("GhostLive", status="failed"),
    ]
    for r in live:
        r.archived = True
    index.upsert(
        {
            "project_name": "IndexOnly",
            "workdir": None,
            "total": 4,
            "by_status": {"done": 4},
            "by_kind": {"task": 4},
            "archived_at": "2026-09-11T01:00:00",
            "last_created_at": "2026-09-10T00:00:00",
        }
    )
    rows = archived_project_aggregates(live, index=index)
    assert {(r["project_name"]) for r in rows} == {"GhostLive", "IndexOnly"}
    ghost = next(r for r in rows if r["project_name"] == "GhostLive")
    assert ghost["total"] == 2
    assert ghost["source"] == "store"
    only = next(r for r in rows if r["project_name"] == "IndexOnly")
    assert only["total"] == 4
    assert only["source"] == "index"


# ---------------------------------------------------------------------------
# HTTP surface: GET filters + aggregates; delete endpoints cascade
# ---------------------------------------------------------------------------
# Cascade: on project / session delete the delegations archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_cascade_archives_its_delegations(tmp_path, tmp_home):
    from sweave.projects import ProjectManager

    pm = ProjectManager()
    workdir = tmp_path / "work"
    workdir.mkdir()
    pm.create_project("Cascade", workdir)

    stores = PerProjectDelegationStores()
    mine = _mk_delegation("Cascade", status="review", session="sess-mine")
    other = _mk_delegation("Cascade", status="review", session="sess-other")
    await _seed_store(stores, workdir, [mine, other])

    n = await archive_session_scope(
        stores, "sess-mine", project_name="Cascade", project_manager=pm
    )
    assert n == 1
    assert _record_archived(stores, mine.delegation_id) is True
    assert _record_archived(stores, other.delegation_id) is False


@pytest.mark.asyncio
async def test_project_cascade_archives_and_writes_index(tmp_path, tmp_home):
    from sweave.projects import ProjectManager

    pm = ProjectManager()
    workdir = tmp_path / "work"
    workdir.mkdir()
    pm.create_project("Proj", workdir)

    stores = PerProjectDelegationStores()
    own1 = _mk_delegation("Proj", status="done")
    own2 = _mk_delegation("Proj", status="failed")
    foreign = _mk_delegation("GhostForeign", status="done")
    await _seed_store(stores, workdir, [own1, own2, foreign])

    index = ArchiveIndexStore(base_dir=tmp_path / "archived")
    n = await archive_project_scope(
        stores, "Proj", index=index, project_manager=pm
    )
    assert n == 2
    assert _record_archived(stores, own1.delegation_id) is True
    assert _record_archived(stores, own2.delegation_id) is True
    # Foreign dead-scope records are the sweep's job, not the cascade's.
    assert _record_archived(stores, foreign.delegation_id) is False

    # Index entry written for the deleted project.
    entry = index.get("Proj")
    assert entry is not None
    assert entry["total"] == 2
    assert entry["source"] == "index"


# ---------------------------------------------------------------------------
# HTTP surface: GET filters + aggregates; delete endpoints cascade
# ---------------------------------------------------------------------------


def _build_app(monkeypatch, tmp_home: Path):
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.web.state import AppState

    original_build = AppState.build

    @classmethod
    def build_with_stub(cls, config_manager):
        state = original_build.__func__(cls, config_manager)

        from sweave.tools import DelegationResult

        class _StubDelegate:
            async def execute(self, agent, task, model=None, task_id=None):
                return DelegationResult(
                    success=True, agent=agent,
                    task_id=task_id or "stub-task",
                    output="ok", error=None,
                )

        state.delegate_tool = _StubDelegate()
        # The lifespan resets delegation_stores anyway (boot sweep
        # operates on the registry the app owns).
        state.delegation_stores = PerProjectDelegationStores()
        return state

    monkeypatch.setattr(AppState, "build", build_with_stub)
    from sweave.web.server import app
    return app


def test_api_default_filters_archived_includes_aggregates(
    monkeypatch, tmp_home
):
    app = _build_app(monkeypatch, tmp_home)
    with TestClient(app) as client:
        state = app.state.app_state

        async def seed():
            store = await state.delegation_stores.for_project(
                Path.home() / ".sweave"
            )
            await store.add(
                Delegation(
                    delegation_id="agg-live", task_id="agg-live",
                    agent="backend", task="live one",
                )
            )
            archived = Delegation(
                delegation_id="agg-arch", task_id="agg-arch",
                agent="backend", task="archived one",
                status="done", project_name="GhostApi",
            )
            await store.add(archived)
            await store.archive_many([archived.delegation_id])

        asyncio.run(seed())

        # Default: archived hidden.
        r = client.get("/api/delegations")
        assert r.status_code == 200
        rows = r.json()["delegations"]
        assert "agg-live" in [d["delegation_id"] for d in rows]
        assert "agg-arch" not in [d["delegation_id"] for d in rows]

        # ?archived=true: only archived rows, archive fields surfaced.
        r = client.get("/api/delegations", params={"archived": "true"})
        assert r.status_code == 200
        rows = r.json()["delegations"]
        assert [d["delegation_id"] for d in rows] == ["agg-arch"]
        assert rows[0]["archived"] is True
        assert rows[0]["archived_at"] is not None

        # ?archived=all: everything.
        r = client.get("/api/delegations", params={"archived": "all"})
        assert r.status_code == 200
        ids = [d["delegation_id"] for d in r.json()["delegations"]]
        assert "agg-live" in ids and "agg-arch" in ids

        # Aggregate surface: the archived record is still counted. The
        # compact Archived group renders from aggregate rows without
        # pulling every archived row.
        r = client.get(
            "/api/delegations", params={"include_archived": "true"}
        )
        assert r.status_code == 200
        body = r.json()
        assert "agg-arch" not in [
            d["delegation_id"] for d in body["delegations"]
        ]
        agg = [
            a for a in body["archived_projects"]
            if a["project_name"] == "GhostApi"
        ]
        assert len(agg) == 1
        assert agg[0]["total"] == 1
        assert agg[0]["by_status"] == {"done": 1}
        assert agg[0]["source"] == "store"
        assert "archived_at" in agg[0]
        assert "last_created_at" in agg[0]


def test_api_rejects_bad_archived_value(monkeypatch, tmp_home):
    app = _build_app(monkeypatch, tmp_home)
    with TestClient(app) as client:
        r = client.get(
            "/api/delegations", params={"archived": "sometimes"}
        )
        assert r.status_code == 400


def test_api_index_only_project_appears_in_aggregates(
    monkeypatch, tmp_home
):
    app = _build_app(monkeypatch, tmp_home)
    with TestClient(app) as client:
        state = app.state.app_state
        # Persisted index row for a project whose records are no
        # longer reachable in any store.
        index = state.archive_index or ArchiveIndexStore()
        index.upsert(
            {
                "project_name": "IndexOnly",
                "workdir": None,
                "total": 4,
                "by_status": {"done": 3, "failed": 1},
                "by_kind": {"task": 4},
                "archived_at": "2026-09-11T01:00:00",
                "last_created_at": "2026-09-10T00:00:00",
            }
        )
        r = client.get(
            "/api/delegations", params={"include_archived": "true"}
        )
        assert r.status_code == 200
        agg = [
            a for a in r.json()["archived_projects"]
            if a["project_name"] == "IndexOnly"
        ]
        assert len(agg) == 1
        assert agg[0]["total"] == 4
        assert agg[0]["by_status"] == {"done": 3, "failed": 1}
        assert agg[0]["source"] == "index"


def test_api_session_delete_endpoint_cascades(monkeypatch, tmp_home):
    app = _build_app(monkeypatch, tmp_home)
    with TestClient(app) as client:
        state = app.state.app_state
        workdir = tmp_home / "work"
        workdir.mkdir()
        cp = client.post(
            "/api/projects",
            json={"name": "CascadeApi", "path": str(workdir)},
        )
        assert cp.status_code == 200, cp.text
        cs = client.post(
            "/api/sessions",
            json={"name": "s1", "project_name": "CascadeApi"},
        )
        assert cs.status_code == 200, cs.text
        session_id = cs.json()["session"]["id"]

        async def seed():
            store = await state.delegation_stores.for_project(workdir)
            await store.add(
                Delegation(
                    delegation_id="del-casc", task_id="del-casc",
                    agent="backend", task="child of s1",
                    status="review", project_name="CascadeApi",
                    parent_session_id=session_id,
                )
            )

        asyncio.run(seed())

        r = client.delete(f"/api/sessions/{session_id}")
        assert r.status_code == 200, r.text
        assert _record_archived(
            state.delegation_stores, "del-casc"
        ) is True


def test_api_project_delete_endpoint_cascades_and_indexes(
    monkeypatch, tmp_home
):
    app = _build_app(monkeypatch, tmp_home)
    with TestClient(app) as client:
        state = app.state.app_state
        workdir = tmp_home / "workp"
        workdir.mkdir()
        cp = client.post(
            "/api/projects",
            json={"name": "CascadeProj", "path": str(workdir)},
        )
        assert cp.status_code == 200, cp.text

        async def seed():
            store = await state.delegation_stores.for_project(workdir)
            await store.add(
                Delegation(
                    delegation_id="del-proj-casc",
                    task_id="del-proj-casc",
                    agent="backend", task="row",
                    status="done", project_name="CascadeProj",
                )
            )

        asyncio.run(seed())

        r = client.delete("/api/projects/CascadeProj")
        assert r.status_code == 200, r.text

        # Record archived in place (not deleted).
        assert _record_archived(
            state.delegation_stores, "del-proj-casc"
        ) is True
        # Aggregate persisted so the tab keeps its stats across a
        # restart even after the workdir's file is cleaned up.
        index = state.archive_index or ArchiveIndexStore()
        entry = index.get("CascadeProj")
        assert entry is not None
        assert entry["total"] == 1
        assert entry["source"] == "index"
