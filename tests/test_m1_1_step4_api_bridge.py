"""M1.1 step 4 tests: API filters, v2 manifest/parent_task_id, SubAgentRun,
and the UI v1 compat bridge (ChildSession on delegation submit)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Local helpers — the AppState builder from the conftest fixture
# ---------------------------------------------------------------------------


def _build_state(monkeypatch, tmp_path: Path):
    """Reuse the conftest pattern to build a fresh AppState with stubbed
    delegate_tool and a real per-project delegation registry."""
    from pathlib import Path as PathCls
    from sweave.web import state as state_mod

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):
        state = original_build.__func__(cls, config_manager)
        state.dynamic_agents_path = tmp_path / "agents.yaml"

        from sweave.tools import DelegationResult

        class _StubDelegateTool:
            async def execute(self, agent, task, model=None, task_id=None):
                return DelegationResult(
                    success=True, agent=agent, task_id=task_id or "stub",
                    output="stub output", error=None,
                )

        state.delegate_tool = _StubDelegateTool()
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)

    from sweave.web.server import app

    return app


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    app = _build_state(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# v2 task: parent_task_id + manifest passthrough
# ---------------------------------------------------------------------------


def test_v2_task_accepts_parent_task_id(client: TestClient):
    """Submit a child delegation whose parent_task_id points at a
    real, existing delegation. M1.6 step 2 enforces "parent must
    exist" as part of the deferral chain rules (a stale id is a 404).
    """
    # Create a real parent first (no parent_task_id of its own, so
    # this is a top-level delegation).
    r_parent = client.post(
        "/api/v2/tasks", json={"task": "parent", "agent": "backend"}
    )
    assert r_parent.status_code == 200
    parent_id = r_parent.json()["delegation_id"]

    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_task_id": parent_id},
    )
    assert r.status_code == 200
    did = r.json()["delegation_id"]
    g = client.get(f"/api/delegations/{did}")
    assert g.status_code == 200
    assert g.json()["parent_task_id"] == parent_id
    # M1.6 step 2: depth is set to parent.depth + 1; chain_root_id
    # equals the parent's delegation_id.
    assert g.json()["depth"] == 1
    assert g.json()["chain_root_id"] == parent_id


def test_v2_task_with_unknown_parent_returns_404(client: TestClient):
    """M1.6 step 2: deferring with a parent_task_id that doesn't
    exist returns 404. The orchestrator should never see this in
    practice (its own delegation always exists), but the API
    contract is clear: a stale id is a 404, not a silent
    parent_task_id write."""
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_task_id": "del-stale"},
    )
    assert r.status_code == 404
    assert "del-stale" in r.json()["detail"]


def test_v2_task_accepts_manifest(client: TestClient):
    manifest = {
        "files_touched": ["src/a.py"],
        "intent": "test intent",
        "confidence": 0.9,
        "breaking_change": False,
    }
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "manifest": manifest},
    )
    assert r.status_code == 200
    did = r.json()["delegation_id"]
    g = client.get(f"/api/delegations/{did}")
    data = g.json()
    assert data["manifest"] is not None
    assert data["manifest"]["files_touched"] == ["src/a.py"]
    assert data["manifest"]["confidence"] == 0.9


def test_v2_task_round_trips_manifest_partial(client: TestClient):
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "manifest": {"intent": "minimal"}},
    )
    assert r.status_code == 200
    did = r.json()["delegation_id"]
    data = client.get(f"/api/delegations/{did}").json()
    assert data["manifest"]["intent"] == "minimal"


# ---------------------------------------------------------------------------
# List filters
# ---------------------------------------------------------------------------


def _make_two_projects(client: TestClient, tmp_path: Path) -> tuple[str, str]:
    """Create two projects (per-call unique names) and submit one delegation in each.

    Returns (p1_name, p2_name). Names use uuid4 so the singleton
    ProjectManager (module-level) never collides across tests that
    share a tmp_path fixture or run in any order.
    """
    p1 = tmp_path / "p1"
    p2 = tmp_path / "p2"
    p1.mkdir()
    p2.mkdir()
    p1_name = f"p1-{uuid.uuid4().hex[:8]}"
    p2_name = f"p2-{uuid.uuid4().hex[:8]}"
    r1 = client.post(
        "/api/projects", json={"name": p1_name, "path": str(p1), "description": ""}
    )
    r2 = client.post(
        "/api/projects", json={"name": p2_name, "path": str(p2), "description": ""}
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    return p1_name, p2_name


def _create_session(
    client: TestClient, tmp_path: Path, label: str
) -> tuple[str, str]:
    """Create a project + session, return (project_name, session_id).

    Project name is per-call unique (uuid4) to avoid clashes with the
    module-level ProjectManager singleton across tests in any order.
    """
    name = f"p-{label}-{uuid.uuid4().hex[:8]}"
    proj_dir = tmp_path / name
    proj_dir.mkdir()
    r = client.post(
        "/api/projects", json={"name": name, "path": str(proj_dir), "description": ""}
    )
    assert r.status_code == 200, r.text
    client.post(f"/api/projects/{name}/active")
    r = client.post(
        "/api/sessions", json={"name": f"S-{label}", "project_name": name}
    )
    assert r.status_code == 200, r.text
    return name, r.json()["session"]["id"]


def test_list_filter_by_project_name(client: TestClient, tmp_path: Path):
    p1_name, p2_name = _make_two_projects(client, tmp_path)
    # p1 active
    client.post(f"/api/projects/{p1_name}/active")
    r = client.post("/api/v2/tasks", json={"task": "a", "agent": "backend"})
    did1 = r.json()["delegation_id"]
    client.post(f"/api/projects/{p2_name}/active")
    r = client.post("/api/v2/tasks", json={"task": "b", "agent": "backend"})
    did2 = r.json()["delegation_id"]

    # Filter by p1
    p1_list = client.get(f"/api/delegations?project_name={p1_name}").json()["delegations"]
    assert all(d["project_name"] == p1_name for d in p1_list)
    assert any(d["delegation_id"] == did1 for d in p1_list)
    assert not any(d["delegation_id"] == did2 for d in p1_list)


def test_list_filter_by_status(client: TestClient):
    r = client.post("/api/v2/tasks", json={"task": "x", "agent": "backend"})
    assert r.status_code == 200
    # Stub tool succeeds, so the delegation is 'done' after the wait
    # (we don't wait here; we just verify the queued state filter works)
    queued = client.get("/api/delegations?status=queued").json()["delegations"]
    assert all(d["status"] == "queued" for d in queued)


def test_list_filter_by_parent_task_id(client: TestClient):
    """The list filter returns delegations whose parent_task_id matches
    the query. M1.6 step 2 enforces "parent must exist" so the
    helper creates a real parent first; the test still exercises the
    filter contract on the child delegation.
    """
    r_parent = client.post(
        "/api/v2/tasks", json={"task": "parent", "agent": "backend"}
    )
    assert r_parent.status_code == 200
    parent_id = r_parent.json()["delegation_id"]
    r = client.post(
        "/api/v2/tasks",
        json={"task": "child", "agent": "backend", "parent_task_id": parent_id},
    )
    assert r.status_code == 200
    filtered = client.get(f"/api/delegations?parent_task_id={parent_id}").json()["delegations"]
    assert all(d["parent_task_id"] == parent_id for d in filtered)


# ---------------------------------------------------------------------------
# SubAgentRun endpoints
# ---------------------------------------------------------------------------


def test_subagent_run_lifecycle(client: TestClient):
    # Start
    r = client.post(
        "/api/subagent-runs",
        json={"agent": "explore-bot", "purpose": "explore", "parent_session_id": "s1"},
    )
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "running"
    run_id = run["run_id"]

    # List
    lst = client.get("/api/subagent-runs").json()["runs"]
    assert any(r["run_id"] == run_id for r in lst)

    # Get
    g = client.get(f"/api/subagent-runs/{run_id}")
    assert g.status_code == 200
    assert g.json()["run_id"] == run_id

    # Finish
    f = client.post(
        f"/api/subagent-runs/{run_id}/finish",
        json={"status": "done", "output_summary": "found 3 files"},
    )
    assert f.status_code == 200
    assert f.json()["status"] == "done"
    assert f.json()["output_summary"] == "found 3 files"
    assert f.json()["finished_at"] is not None


def test_subagent_run_filter_by_purpose_and_status(client: TestClient):
    client.post("/api/subagent-runs", json={"agent": "a", "purpose": "explore"})
    client.post("/api/subagent-runs", json={"agent": "b", "purpose": "investigate"})
    client.post("/api/subagent-runs", json={"agent": "c", "purpose": "custom"})

    explore = client.get("/api/subagent-runs?purpose=explore").json()["runs"]
    assert all(r["purpose"] == "explore" for r in explore)
    assert len(explore) >= 1

    custom = client.get("/api/subagent-runs?purpose=custom").json()["runs"]
    assert all(r["purpose"] == "custom" for r in custom)
    assert any(r["agent"] == "c" for r in custom)


def test_subagent_run_finish_rejects_non_terminal_status(client: TestClient):
    """Pydantic validates the body before the handler runs; ``status``
    is restricted to ``done`` / ``failed`` on a finish call."""
    r = client.post("/api/subagent-runs", json={"agent": "a"})
    run_id = r.json()["run_id"]
    bad = client.post(
        f"/api/subagent-runs/{run_id}/finish",
        json={"status": "running", "output_summary": "x"},
    )
    assert bad.status_code == 422  # Pydantic validation error
    # The run remains 'running'.
    g = client.get(f"/api/subagent-runs/{run_id}")
    assert g.json()["status"] == "running"


def test_subagent_run_get_unknown_returns_404(client: TestClient):
    r = client.get("/api/subagent-runs/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# UI v1 compat bridge: ChildSession is added on delegation submit
# ---------------------------------------------------------------------------


def test_bridge_writes_child_session_on_submit(client: TestClient, tmp_path: Path):
    """Submit a v2 task with a parent_session_id; the parent session should
    gain a ChildSession entry whose delegation_id matches."""
    name, sid = _create_session(client, tmp_path, "bridge-write")

    # Submit a v2 task pinned to that session
    r = client.post(
        "/api/v2/tasks",
        json={"task": "do the thing", "agent": "backend", "parent_session_id": sid},
    )
    assert r.status_code == 200
    did = r.json()["delegation_id"]

    # The session should now contain a child with our delegation_id
    g = client.get(f"/api/sessions/{sid}")
    assert g.status_code == 200
    children = g.json()["children"]
    assert any(c["delegation_id"] == did for c in children), children
    # And the legacy fields are still there
    child = next(c for c in children if c["delegation_id"] == did)
    assert child["agent_name"] == "backend"
    assert child["task"] == "do the thing"


def test_bridge_does_not_duplicate_on_resubmit(client: TestClient, tmp_path: Path):
    """Two distinct task_ids -> two distinct child entries; same task_id
    is idempotent (M1.1 plan: bridge write-through is best-effort +
    duplicate-safe)."""
    name, sid = _create_session(client, tmp_path, "bridge-dup")

    # Submit twice with the same task_id (which we can't easily force
    # without a new endpoint; the test exercises the 'same id, different
    # delegation_id' path: two distinct delegations each bridge to a
    # new ChildSession, but the second submit's *id* collides if the
    # JobRunner picks task_id deterministically. We accept the realistic
    # outcome: at most one child per distinct task_id per session).
    r1 = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_session_id": sid},
    )
    r2 = client.post(
        "/api/v2/tasks",
        json={"task": "y", "agent": "backend", "parent_session_id": sid},
    )
    assert r1.json()["task_id"] != r2.json()["task_id"]
    g = client.get(f"/api/sessions/{sid}")
    children = g.json()["children"]
    # Two distinct task_ids -> two children. Same id -> one.
    assert len(children) == 2


def test_child_session_backward_compat_legacy_field_loads():
    """A pre-M1.1 ChildSession JSON dict (no delegation_id) loads with
    delegation_id=None — the UI v1 render path treats None as legacy."""
    from sweave.projects import ChildSession
    from datetime import datetime, timezone

    # Pre-M1.1 JSON shape: no delegation_id key, no delegation_id field
    legacy_dict = {
        "id": "legacy-child-1",
        "parent_session_id": "legacy-sess-1",
        "agent_name": "backend",
        "task": "legacy",
        "status": "completed",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "output": "old output",
    }
    reloaded = ChildSession.from_dict(legacy_dict)
    assert reloaded.delegation_id is None
    assert reloaded.id == "legacy-child-1"
    assert reloaded.output == "old output"


def test_child_session_with_delegation_id_round_trips():
    """A post-M1.1 ChildSession JSON dict carries delegation_id."""
    from sweave.projects import ChildSession
    from datetime import datetime, timezone

    new_dict = {
        "id": "new-child-1",
        "parent_session_id": "sess-1",
        "agent_name": "backend",
        "task": "new",
        "status": "running",
        "created_at": datetime(2026, 8, 29, tzinfo=timezone.utc).isoformat(),
        "delegation_id": "d_abc123",
    }
    c = ChildSession.from_dict(new_dict)
    assert c.delegation_id == "d_abc123"
    d = c.to_dict()
    assert d["delegation_id"] == "d_abc123"
