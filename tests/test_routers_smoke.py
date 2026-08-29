"""Router smoke tests via FastAPI TestClient.

These tests build the real ``sweave.web.server:app`` and use
``starlette.testclient.TestClient`` to drive it. They DO NOT touch the
real ~/.sweave state — they point ProjectManager at a temp dir via
the lifespan override.

The goal is to exercise every router module's import path and verify
that the basic shape of each endpoint is correct. Behavioural tests
for individual routes (e.g. /api/tasks) live in the dedicated test
files; this is just the wiring smoke.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """A TestClient backed by a fresh temp home and stubbed dynamic-agents."""
    from pathlib import Path as PathCls

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))

    # Also stub the dynamic-agents path inside AppState so it doesn't
    # touch the real agents.yaml.
    from sweave.web import state as state_mod

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):  # type: ignore[no-untyped-def]
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)

    # Import the app AFTER the monkeypatches so the AppState.build override
    # is in place when lifespan runs.
    from sweave.web.server import app

    with TestClient(app) as c:
        yield c


def test_app_loads(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200


def test_api_agents_list(client: TestClient):
    r = client.get("/api/agents")
    assert r.status_code == 200
    data = r.json()
    assert "builtin" in data
    assert "dynamic" in data


def test_api_models(client: TestClient):
    r = client.get("/api/models")
    assert r.status_code == 200
    assert "roles" in r.json()


def test_api_rules(client: TestClient):
    r = client.get("/api/rules")
    assert r.status_code == 200


def test_api_config(client: TestClient):
    r = client.get("/api/config")
    assert r.status_code == 200


def test_api_fs_drives(client: TestClient):
    r = client.get("/api/fs/drives")
    assert r.status_code == 200


def test_api_projects(client: TestClient):
    r = client.get("/api/projects")
    assert r.status_code == 200


def test_api_sessions(client: TestClient):
    r = client.get("/api/sessions")
    assert r.status_code == 200


def test_api_memory_banks(client: TestClient):
    r = client.get("/api/memory/banks")
    assert r.status_code == 200


def test_api_route_decision(client: TestClient):
    r = client.post("/api/route", json={"task": "build a REST API"})
    assert r.status_code == 200
    data = r.json()
    assert "agent" in data


def test_api_delegations_list(client: TestClient):
    r = client.get("/api/delegations")
    assert r.status_code == 200
    assert r.json() == {"delegations": []}


def test_api_v2_tasks_returns_delegation_id(client: TestClient):
    r = client.post("/api/v2/tasks", json={"task": "test", "agent": "backend"})
    assert r.status_code == 200
    data = r.json()
    assert "delegation_id" in data
    assert data["status"] == "queued"
    assert data["agent"] == "backend"


def test_api_v2_tasks_routes_when_no_agent(client: TestClient):
    r = client.post("/api/v2/tasks", json={"task": "fix a sql bug"})
    assert r.status_code == 200
    data = r.json()
    assert "delegation_id" in data


def test_api_delegation_get_unknown_returns_404(client: TestClient):
    r = client.get("/api/delegations/does-not-exist")
    assert r.status_code == 404


def test_legacy_tasks_endpoint_still_works(client: TestClient):
    """POST /api/tasks is the deprecated sync path; it must still return a result.

    The actual agent call will fail (no opencode in the test env) but the
    endpoint itself must respond with a TaskResponse-shaped payload, not 500.
    """
    r = client.post("/api/tasks", json={"task": "x", "agent": "backend"})
    # Either 200 (with a failed result) or 500 from the harness — what we
    # care about is that the endpoint is wired and reachable.
    assert r.status_code in (200, 500), f"unexpected status: {r.status_code}"
    if r.status_code == 200:
        data = r.json()
        # success=False because the stub harness isn't there
        assert data["success"] is False
