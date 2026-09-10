"""M1.12 step 1: ``PUT /api/projects/{name}/permission_roots`` endpoint.

Same TestClient pattern as tests/test_routers_smoke.py (temp home
override so the AppState build and config reads stay off the real
~/.sweave; the conftest autouse fixture isolates the project
manager singleton).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from sweave.web import state as state_mod

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):  # type: ignore[no-untyped-def]
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)

    from sweave.web.server import app

    with TestClient(app) as c:
        yield c


def test_permission_roots_endpoint_roundtrip(client):
    proj_dir = Path(tempfile.mkdtemp())
    r = client.post(
        "/api/projects",
        json={"name": "roots-proj-1", "path": str(proj_dir)},
    )
    assert r.status_code == 200, r.text
    r = client.put(
        "/api/projects/roots-proj-1/permission_roots",
        json={"roots": ["/tmp/shareA", "~/shareB"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["permission_roots"] == ["/tmp/shareA", "~/shareB"]
    r = client.get("/api/projects/roots-proj-1")
    assert r.json()["permission_roots"] == ["/tmp/shareA", "~/shareB"]
    # Unknown project -> 404.
    r = client.put(
        "/api/projects/nope-missing/permission_roots",
        json={"roots": []},
    )
    assert r.status_code == 404
