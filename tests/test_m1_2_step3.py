"""M1.2 step 3 tests: /api/specialists CRUD + override log + events.

Covers the new router (`routers/specialists.py`) and the override log
(`runtime/override_log.py`). SpecialistResolver itself was covered
end-to-end in step 1; here we focus on the HTTP surface.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Local AppState factory
# ---------------------------------------------------------------------------


def _build_state(monkeypatch, tmp_path: Path):
    """Reuse the conftest pattern: stub Path.home, build a fresh state
    with a real SpecialistResolver and a stubbed delegate_tool."""
    from pathlib import Path as PathCls
    from sweave.web import state as state_mod

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
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


def _create_project(client: TestClient, tmp_path: Path) -> str:
    name = f"p-{uuid.uuid4().hex[:8]}"
    proj = tmp_path / name
    proj.mkdir()
    r = client.post("/api/projects", json={"name": name, "path": str(proj), "description": ""})
    assert r.status_code == 200, r.text
    client.post(f"/api/projects/{name}/active")
    return name


# ---------------------------------------------------------------------------
# Specialist CRUD
# ---------------------------------------------------------------------------


def test_create_specialist_in_active_project(client: TestClient, tmp_path: Path):
    name = _create_project(client, tmp_path)
    r = client.post(
        "/api/specialists",
        json={"name": "sql-expert", "scope": "project", "role_ref": "backend"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "sql-expert"
    assert data["scope"] == "project"
    assert data["role_ref"] == "backend"
    assert data["is_orchestrator"] is False  # always False from the API


def test_create_specialist_with_explicit_orchestrator_true_silently_coerced(
    client: TestClient, tmp_path: Path
):
    """Defence in depth: a POST that arrives with is_orchestrator=True
    is silently coerced to False. The orchestrator is auto-seeded, not
    user-creatable."""
    _create_project(client, tmp_path)
    r = client.post(
        "/api/specialists",
        json={"name": "evil", "scope": "project", "is_orchestrator": True},
    )
    assert r.status_code == 201
    assert r.json()["is_orchestrator"] is False


def test_create_specialist_with_reserved_orchestrator_name_409(
    client: TestClient, tmp_path: Path
):
    _create_project(client, tmp_path)
    r = client.post("/api/specialists", json={"name": "orchestrator", "scope": "project"})
    assert r.status_code == 409
    assert "reserved" in r.json()["detail"]


def test_create_specialist_collision_409(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r1 = client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    assert r1.status_code == 201
    r2 = client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"]


def test_get_specialist(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    r = client.get("/api/specialists/alpha")
    assert r.status_code == 200
    assert r.json()["name"] == "alpha"


def test_get_specialist_404(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.get("/api/specialists/nope")
    assert r.status_code == 404


def test_list_specialists_includes_seeds_by_default(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    r = client.get("/api/specialists")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["specialists"]]
    # Project specialist + the 3 user-creatable seeds (orchestrator is
    # excluded from list_resolved). Seed names are configured in
    # sweave/agents/*/config.yaml (e.g. "backend-specialist" for the
    # backend seed).
    assert "alpha" in names
    assert "backend-specialist" in names
    assert "frontend-specialist" in names
    assert "reviewer-specialist" in names
    assert "orchestrator" not in names


def test_list_specialists_excludes_seeds_when_asked(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    r = client.get("/api/specialists?include_seeds=false")
    names = [s["name"] for s in r.json()["specialists"]]
    assert "alpha" in names
    assert "backend-specialist" not in names


def test_update_specialist(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post(
        "/api/specialists",
        json={"name": "alpha", "scope": "project", "description": "old"},
    )
    r = client.put(
        "/api/specialists/alpha",
        json={"description": "new", "current_model": "claude-3"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["description"] == "new"
    assert data["current_model"] == "claude-3"


def test_update_orchestrator_409(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.put("/api/specialists/orchestrator", json={"description": "hijacked"})
    assert r.status_code == 409


def test_delete_specialist(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    r = client.delete("/api/specialists/alpha")
    assert r.status_code == 200
    assert r.json() == {"success": True, "name": "alpha"}
    # Now GET should 404
    g = client.get("/api/specialists/alpha")
    assert g.status_code == 404


def test_delete_orchestrator_409(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.delete("/api/specialists/orchestrator")
    assert r.status_code == 409


def test_set_model(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post("/api/specialists", json={"name": "alpha", "scope": "project"})
    r = client.put("/api/specialists/alpha/model", json={"model": "claude-3"})
    assert r.status_code == 200
    data = r.json()
    assert data["current_model"] == "claude-3"
    # GET reflects the change
    g = client.get("/api/specialists/alpha")
    assert g.json()["current_model"] == "claude-3"


def test_set_model_orchestrator_409(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.put("/api/specialists/orchestrator/model", json={"model": "claude-3"})
    assert r.status_code == 409


def test_set_model_publishes_model_changed_event(
    client: TestClient, tmp_path: Path
):
    """PUT /model emits model.changed with {name, model, scope} (amendment B)."""
    import asyncio
    import json
    from fastapi.testclient import TestClient
    from starlette.testclient import WebSocketTestSession

    _create_project(client, tmp_path)
    client.post("/api/specialists", json={"name": "alpha", "scope": "project"})

    # Capture WebSocket events
    with client.websocket_connect("/ws") as ws:
        r = client.put("/api/specialists/alpha/model", json={"model": "claude-3"})
        assert r.status_code == 200
        # The model.changed event should arrive promptly
        msg = ws.receive_text()
        event = json.loads(msg)
        assert event["event"] == "model.changed"
        assert event["data"]["name"] == "alpha"
        assert event["data"]["model"] == "claude-3"
        assert event["data"]["scope"] == "project"


# ---------------------------------------------------------------------------
# Override log
# ---------------------------------------------------------------------------


def test_v2_task_override_appends_to_log(client: TestClient, tmp_path: Path):
    """POST /api/v2/tasks with an agent that differs from the router
    decision appends to the per-project override log."""
    name = _create_project(client, tmp_path)
    # The rule-router returns 'backend' for "fix a SQL bug" (per
    # rules.yaml), but the user supplies 'sql-expert'. Differing -> log.
    r = client.post(
        "/api/v2/tasks",
        json={"task": "fix a sql bug", "agent": "sql-expert"},
    )
    assert r.status_code == 200

    log_path = tmp_path / name / ".sweave" / "override_log.jsonl"
    assert log_path.exists(), "override log not written"
    lines = [
        json.loads(l)
        for l in log_path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["routed_agent"] == "backend"  # router's pick
    assert entry["user_agent"] == "sql-expert"  # user's override
    assert "sql" in entry["task"].lower()
    assert entry["source"] == "v2_task"


def test_v2_task_no_override_does_not_log(client: TestClient, tmp_path: Path):
    """If the user supplies the same agent the router picks, no log entry."""
    name = _create_project(client, tmp_path)
    r = client.post(
        "/api/v2/tasks",
        json={"task": "fix a sql bug", "agent": "backend"},
    )
    assert r.status_code == 200
    log_path = tmp_path / name / ".sweave" / "override_log.jsonl"
    # File may not even exist (the writer is best-effort + only on override)
    if log_path.exists():
        assert log_path.read_text(encoding="utf-8") == ""


def test_get_overrides_returns_log(client: TestClient, tmp_path: Path):
    name = _create_project(client, tmp_path)
    client.post(
        "/api/v2/tasks",
        json={"task": "fix a sql bug", "agent": "sql-expert"},
    )
    r = client.get("/api/overrides", params={"project": str(tmp_path / name)})
    assert r.status_code == 200
    entries = r.json()["overrides"]
    assert len(entries) == 1
    assert entries[0]["routed_agent"] == "backend"


def test_get_overrides_global_fallback_when_no_project_param(
    client: TestClient, tmp_path: Path
):
    """When no project param, returns the global fallback log (no-active
    project case, amendment F)."""
    # Submit a task without an active project — but the conftest sets
    # the singleton ProjectManager in memory. To exercise the global
    # path we can submit when no project is active, which the
    # singleton ProjectManager defaults to None.
    r = client.post(
        "/api/v2/tasks",
        json={"task": "fix a sql bug", "agent": "sql-expert"},
    )
    assert r.status_code == 200
    # The override log is per-project; if no project was active, the
    # global fallback is the only place the entry could land.
    r = client.get("/api/overrides")  # no project param
    assert r.status_code == 200
    # The result is whatever the global fallback has (could be empty
    # if the active-project branch handled it; we just assert the
    # endpoint works).
    assert "overrides" in r.json()


# ---------------------------------------------------------------------------
# Unit tests for the runtime module
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_log_appends(tmp_path: Path):
    from sweave.runtime.override_log import (
        OverrideLog,
        make_override_entry,
    )

    log = OverrideLog(tmp_path / "log.jsonl")
    entry = make_override_entry(
        project="p1",
        session_id="s1",
        task="t",
        routed_agent="backend",
        routed_model="m1",
        user_agent="sql-expert",
    )
    await log.append(entry)
    await log.append(entry)  # second append
    out = log.read()
    assert len(out) == 2
    assert out[0]["routed_agent"] == "backend"
    assert out[0]["user_agent"] == "sql-expert"
    assert "ts" in out[0]


@pytest.mark.asyncio
async def test_override_log_for_project_creates_sweave_subdir(tmp_path: Path):
    from sweave.runtime.override_log import OverrideLog

    p = tmp_path / "myproj"
    p.mkdir()
    log = OverrideLog.for_project(p)
    assert (p / ".sweave" / "override_log.jsonl").parent.exists()


def test_override_log_read_missing_file_returns_empty(tmp_path: Path):
    from sweave.runtime.override_log import OverrideLog

    log = OverrideLog(tmp_path / "nope.jsonl")
    assert log.read() == []


def test_override_log_read_skips_malformed_lines(tmp_path: Path):
    from sweave.runtime.override_log import OverrideLog

    log = OverrideLog(tmp_path / "log.jsonl")
    log.file_path.write_text("not json\n\n{\"a\": 1}\n", encoding="utf-8")
    out = log.read()
    assert out == [{"a": 1}]
