"""M1.2 step 4 tests: /api/agents bridge + render-shape + description bug.

The plan says test-first: the UI's ``loadAgents()`` reads
``data.builtin.map()`` / ``data.global.map()`` but the router returns
dicts. This test captures the shape contract; the bug-fix commit
will make the router return arrays so the UI renders.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _build_state(monkeypatch, tmp_path: Path):
    """Same conftest pattern: stub Path.home + AppState.build with a stubbed
    delegate_tool so the v2 task endpoint doesn't try to spawn opencode."""
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
# /api/agents shape contract (the render bug)
# ---------------------------------------------------------------------------


def test_list_agents_returns_arrays_for_builtin_global_dynamic(client: TestClient):
    """The /api/agents response shape that app.js:366-367 expects:
    ``{builtin: [...], global: [...], dynamic: [...]}`` -- all three are
    arrays, not dicts. This is the contract that fixes the render bug
    (the legacy router returned dicts; ``dict.map`` is undefined and the
    UI silently showed 'No agents configured.').
    """
    r = client.get("/api/agents")
    assert r.status_code == 200
    data = r.json()

    # Three keys, all array-typed.
    assert "builtin" in data
    assert "global" in data
    assert "dynamic" in data
    assert isinstance(data["builtin"], list), (
        f"builtin must be a list for app.js's data.builtin.map() to work; "
        f"got {type(data['builtin']).__name__}"
    )
    assert isinstance(data["global"], list), (
        f"global must be a list for app.js's data.global.map() to work; "
        f"got {type(data['global']).__name__}"
    )
    assert isinstance(data["dynamic"], list), (
        f"dynamic must be a list; got {type(data['dynamic']).__name__}"
    )


def test_list_agents_builtin_excludes_orchestrator(client: TestClient):
    """The orchestrator is the supervisor, not a routing-pool member; it
    must not appear in the Agents tab list."""
    r = client.get("/api/agents")
    names = [a["name"] for a in r.json()["builtin"]]
    assert "orchestrator" not in names
    # The other 3 roles are present (config/models.yaml seeds them)
    for role in ("backend", "frontend", "reviewer"):
        assert role in names, f"{role} not in {names}"


def test_list_agents_global_excludes_orchestrator_and_seeds(
    client: TestClient, tmp_path: Path
):
    """``global`` is the resolved-specialist pool minus the orchestrator
    and minus the seed views (per the plan: 'global = resolved
    specialists excluding seeds and orchestrator'). It DOES include
    project-scoped specialists (they're in the routing pool) AND
    global-scoped specialists."""
    _create_project(client, tmp_path)
    # Add a project specialist + a global specialist
    r = client.post("/api/specialists", json={"name": "sql-expert", "scope": "project"})
    assert r.status_code == 201
    state = client.app.state.app_state
    from sweave.runtime.specialist_store import Specialist
    state.ensure_specialist_resolver().create(
        Specialist(name="global-one", scope="global", current_model="m1"),
    )
    r = client.get("/api/agents")
    global_names = [a["name"] for a in r.json()["global"]]
    # Both the project and the global specialist are in the routing pool
    assert "sql-expert" in global_names
    assert "global-one" in global_names
    # Orchestrator + seed views are excluded (per the plan)
    assert "orchestrator" not in global_names
    assert "backend-specialist" not in global_names
    assert "frontend-specialist" not in global_names
    assert "reviewer-specialist" not in global_names


def test_list_agents_each_entry_has_required_fields(client: TestClient):
    """Every entry in global/dynamic must carry the fields the UI
    renders. The 'builtin' entries (role tier) don't have a harness --
    they reference models.yaml, not a Specialist record.
    app.js:380-382 reads agent.role, agent.model, agent.harness;
    the harness line is in a separate render branch that only fires
    for non-builtin entries (the UI checks ``if (agent.harness)``).
    """
    r = client.get("/api/agents")
    for entry in r.json()["global"] + r.json()["dynamic"]:
        assert "name" in entry
        assert "role" in entry
        assert "model" in entry
        assert "harness" in entry
    # Built-in: name + role + model (role-tier info)
    for entry in r.json()["builtin"]:
        assert "name" in entry
        assert "role" in entry
        assert "model" in entry


def test_app_js_loadagents_does_not_throw_with_bridge(client: TestClient):
    """Simulate the UI's loadAgents() consumer: ``[...data.builtin,
    ...data.global].map(a => card)`` must work without TypeError.

    The pre-fix code threw ``TypeError: dict.map is not a function``
    on the legacy shape; the test asserts the post-fix shape supports
    the same array-spread map pattern.
    """
    r = client.get("/api/agents")
    data = r.json()
    # The pattern from app.js:365-368:
    try:
        all_agents = [
            *({**a, "scope": "builtin"} for a in data["builtin"]),
            *({**a, "scope": "global"} for a in data["global"]),
        ]
    except TypeError as e:
        pytest.fail(f"app.js pattern failed: {e}")
    # And every entry got the scope stamped
    for a in all_agents:
        assert "scope" in a


# ---------------------------------------------------------------------------
# description-overwrite regression test
# ---------------------------------------------------------------------------


def test_update_agent_does_not_overwrite_prompt_with_description(
    client: TestClient, tmp_path: Path
):
    """The legacy bug (routers/agents.py:131-134) wrote
    ``spec.system_prompt = update.description`` whenever description
    was non-empty. The fix routes the description-only update through
    the new specialist store, where ``description`` and ``system_prompt``
    are distinct fields and never conflated.

    The /api/agents bridge delegates to the specialist store now; the
    bug is gone because the underlying record doesn't have the
    bug-prone branch.
    """
    name = _create_project(client, tmp_path)
    # Create a dynamic agent with a known prompt
    r = client.post(
        "/api/agents",
        json={
            "name": "alpha",
            "role": "tester",
            "model": "test-model",
            "system_prompt": "ORIGINAL PROMPT",
            "description": "ORIGINAL DESCRIPTION",
        },
    )
    assert r.status_code == 200

    # Update only the description (no system_prompt in the body)
    r = client.put(
        "/api/agents/alpha",
        json={"description": "NEW DESCRIPTION"},
    )
    assert r.status_code == 200

    # The stored prompt must still be the original (not overwritten by description)
    state = client.app.state.app_state
    rec = state.ensure_specialist_resolver().resolve(
        "alpha", project_dir=Path(tmp_path) / name
    )
    assert rec is not None
    assert rec.system_prompt == "ORIGINAL PROMPT", (
        f"description must not overwrite system_prompt; got {rec.system_prompt!r}"
    )
    assert rec.description == "NEW DESCRIPTION"


# ---------------------------------------------------------------------------
# CRUD through the bridge
# ---------------------------------------------------------------------------


def test_create_agent_through_bridge_creates_specialist_record(
    client: TestClient, tmp_path: Path
):
    name = _create_project(client, tmp_path)
    r = client.post(
        "/api/agents",
        json={
            "name": "sql-expert",
            "role": "tester",  # not a built-in role -> allowed
            "model": "gpt-4",
            "system_prompt": "you are a sql expert",
        },
    )
    assert r.status_code == 200
    # The specialist record exists in the per-project store
    state = client.app.state.app_state
    rec = state.ensure_specialist_resolver().resolve(
        "sql-expert", project_dir=Path(tmp_path) / name
    )
    assert rec is not None
    assert rec.system_prompt == "you are a sql expert"
    assert rec.current_model == "gpt-4"


def test_create_agent_collision_400(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post(
        "/api/agents",
        json={"name": "alpha", "role": "tester", "model": "m", "system_prompt": "p"},
    )
    r = client.post(
        "/api/agents",
        json={"name": "alpha", "role": "tester", "model": "m", "system_prompt": "p"},
    )
    assert r.status_code == 400


def test_create_agent_built_in_role_400(client: TestClient, tmp_path: Path):
    """Roles that are built-in (in models.yaml) cannot be re-created via
    the legacy endpoint; the user uses /api/specialists for that."""
    _create_project(client, tmp_path)
    r = client.post(
        "/api/agents",
        json={"name": "alpha", "role": "backend", "model": "m", "system_prompt": "p"},
    )
    assert r.status_code == 400


def test_get_agent_built_in(client: TestClient):
    """GET /api/agents/{name} for a built-in role returns its info."""
    r = client.get("/api/agents/backend")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "backend"
    assert "model" in data


def test_get_agent_dynamic(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    client.post(
        "/api/agents",
        json={"name": "alpha", "role": "tester", "model": "m", "system_prompt": "p"},
    )
    r = client.get("/api/agents/alpha")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "alpha"


def test_get_agent_orchestrator_returns_info(client: TestClient, tmp_path: Path):
    """The orchestrator is reachable via GET /api/agents/orchestrator.
    Its 'role' is 'orchestrator' (matches the singleton auto-seed)."""
    _create_project(client, tmp_path)
    r = client.get("/api/agents/orchestrator")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "orchestrator"
    assert data["role"] == "orchestrator"
    assert "model" in data  # from the role default


def test_get_agent_unknown_404(client: TestClient):
    r = client.get("/api/agents/does-not-exist")
    assert r.status_code == 404


def test_delete_agent_removes_specialist_record(client: TestClient, tmp_path: Path):
    name = _create_project(client, tmp_path)
    client.post(
        "/api/agents",
        json={"name": "alpha", "role": "tester", "model": "m", "system_prompt": "p"},
    )
    r = client.delete("/api/agents/alpha")
    assert r.status_code == 200
    state = client.app.state.app_state
    assert state.ensure_specialist_resolver().resolve(
        "alpha", project_dir=Path(tmp_path) / name
    ) is None


def test_delete_agent_orchestrator_409(client: TestClient, tmp_path: Path):
    _create_project(client, tmp_path)
    r = client.delete("/api/agents/orchestrator")
    assert r.status_code == 409


def test_create_agent_publishes_specialist_created_event(
    client: TestClient, tmp_path: Path
):
    _create_project(client, tmp_path)
    with client.websocket_connect("/ws") as ws:
        client.post(
            "/api/agents",
            json={"name": "alpha", "role": "tester", "model": "m", "system_prompt": "p"},
        )
        msg = ws.receive_text()
        event = json.loads(msg)
        # The bridge maps the legacy 'agent_created' name onto the new
        # 'specialist.created' vocabulary (M1.prep legacy aliases still
        # work; new vocabulary added in step 3).
        assert event["event"] in ("agent_created", "specialist.created")
        assert event["data"]["name"] == "alpha"
