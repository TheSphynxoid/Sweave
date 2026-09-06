"""R4.1 step 1b: backend WS events for project + session mutations.

The foundation nav (sidebar) needs WS events to refresh
without polling. Five events are published from
``sweave/web/routers/projects.py``:

  - ``project.created``        (POST /api/projects)
  - ``project.deleted``        (DELETE /api/projects/{name})
  - ``session.created``        (POST /api/sessions)
  - ``session.deleted``        (DELETE /api/sessions/{id})
  - ``active_session.changed`` (POST /api/sessions/{id}/active)

These tests drive the routes through FastAPI's TestClient
(real lifespan, real state) and substitute a mock
``event_bus`` via dependency injection so the publish calls
are observable. The bus is stashed on a module-level handle
that the test reads back (the AppState is held by the
TestClient's app and not directly accessible after the
lifespan returns).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


class _MockBus:
    """A drop-in WSEventBus replacement that records every
    ``publish`` call as an AsyncMock. The router calls
    ``state.publish(event, data)`` -- the AppState helper that
    fans the call out to ``event_bus.publish(event, data)``.

    Carries a ``_subscribers`` list because the server's
    lifespan teardown iterates it to close any open WS
    connections cleanly. The test's mock bus has no real
    subscribers; the empty list is enough.
    """

    def __init__(self) -> None:
        self.publish = AsyncMock()
        self._subscribers: list = []


# Module-level handle the fixture sets; tests read it back.
_CURRENT_BUS: _MockBus | None = None


def _get_bus() -> _MockBus:
    if _CURRENT_BUS is None:
        raise RuntimeError("test fixture did not install a mock bus")
    return _CURRENT_BUS


@pytest.fixture
def client_and_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    """A TestClient backed by a fresh temp home + a mock event bus.

    The mock bus is wired in by replacing ``WSEventBus`` with a
    recording class -- the server's lifespan creates a new
    ``WSEventBus()`` and assigns it to ``state.event_bus``
    (server.py:98, AFTER ``AppState.build`` returns), so the
    only reliable hook is the class itself. The fixture's bus
    is exposed via the ``_get_bus()`` helper.

    Yields (TestClient, tmp_path) so each test can create
    project directories inside the temp area.
    """
    global _CURRENT_BUS
    from sweave.web import state as state_mod

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # A single shared mock bus; the WSEventBus class
    # replacement below returns this same instance for every
    # instantiation (the lifespan calls WSEventBus() once,
    # so the test gets one consistent bus per fixture).
    bus = _MockBus()
    _CURRENT_BUS = bus

    class _MockBusFactory:
        """Stand-in for ``WSEventBus``. The lifespan calls
        ``WSEventBus()`` to create the bus; we return the
        shared mock instance so all publish calls land on
        the same recordable object.
        """

        def __new__(cls, *args, **kwargs):  # type: ignore[no-untyped-def]
            return bus

    # Patch WSEventBus at the source module. The server
    # imports it lazily inside the lifespan
    # (``from sweave.web.events import WSEventBus``); the
    # routers use the bus via the state object, so a single
    # class patch is enough.
    import sweave.web.events as events_mod

    monkeypatch.setattr(events_mod, "WSEventBus", _MockBusFactory)

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):  # type: ignore[no-untyped-def]
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        # Stub the dynamic-agents path (same as test_routers_smoke).
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        # Replace delegate_tool with a stub so /api/v2/tasks
        # (if exercised) doesn't try to spawn opencode.
        from sweave.tools import DelegationResult

        class _StubDelegateTool:
            async def execute(self, agent, task, model=None, task_id=None):
                return DelegationResult(
                    success=False, agent=agent, task_id=task_id or "stub",
                    output="", error="stubbed in test",
                )

        state.delegate_tool = _StubDelegateTool()
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)

    from sweave.web.server import app

    with TestClient(app) as c:
        # The lifespan has run by now; WSEventBus() returned
        # the shared mock bus (server.py:98 assigns it to
        # state.event_bus). Every publish call lands on the
        # AsyncMock that ``_get_bus()`` returns.
        assert _CURRENT_BUS is not None
        yield c, tmp_path
        _CURRENT_BUS = None


def _published(bus: _MockBus, event_name: str) -> list[dict]:
    """Return the data payloads for every ``publish`` call whose
    event name matches. The AsyncMock records both positional
    and keyword args; we read call.args[1] (the data dict).
    """
    out = []
    for call in bus.publish.call_args_list:
        args = call.args
        if not args:
            continue
        name = args[0]
        if name == event_name:
            payload = args[1] if len(args) > 1 else {}
            out.append(payload)
    return out


# ---------------------------------------------------------------------------
# Project events
# ---------------------------------------------------------------------------


def test_create_project_publishes_project_created(client_and_tmp) -> None:
    client, tmp_path = client_and_tmp
    bus = _get_bus()
    # Unique name per test (the project_manager is a module
    # singleton shared across tests; test isolation via the
    # tmp_path monkeypatch only affects Path.home() reads at
    # construction, not the manager's persisted state).
    name = f"proj-create-{id(bus)}"
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/api/projects",
        json={"name": name, "path": str(proj_dir)},
    )
    assert r.status_code == 200, r.text
    # Diagnostic: see what the bus actually recorded.
    print("BUS CALLS:", [(c.args[0] if c.args else None) for c in bus.publish.call_args_list])
    created = _published(bus, "project.created")
    assert len(created) == 1
    assert created[0]["name"] == name
    assert created[0]["path"] == str(proj_dir)


def test_delete_project_publishes_project_deleted(client_and_tmp) -> None:
    client, tmp_path = client_and_tmp
    bus = _get_bus()
    name = f"proj-delete-{id(bus)}"
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/api/projects",
        json={"name": name, "path": str(proj_dir)},
    )
    assert r.status_code == 200
    bus.publish.call_args_list.clear()

    r = client.delete(f"/api/projects/{name}")
    assert r.status_code == 200
    deleted = _published(bus, "project.deleted")
    assert len(deleted) == 1
    assert deleted[0]["name"] == name


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------


def test_create_session_publishes_session_created(client_and_tmp) -> None:
    client, tmp_path = client_and_tmp
    bus = _get_bus()
    pname = f"proj-sess-c-{id(bus)}"
    proj_dir = tmp_path / pname
    proj_dir.mkdir(parents=True, exist_ok=True)
    client.post(
        "/api/projects",
        json={"name": pname, "path": str(proj_dir)},
    )
    client.post(f"/api/projects/{pname}/active", json={})
    bus.publish.call_args_list.clear()

    r = client.post("/api/sessions", json={"name": "sess-1", "project_name": pname})
    assert r.status_code == 200
    created = _published(bus, "session.created")
    assert len(created) == 1
    payload = created[0]
    assert payload["name"] == "sess-1"
    assert payload["project_name"] == pname
    assert "id" in payload and payload["id"]


def test_delete_session_publishes_session_deleted(client_and_tmp) -> None:
    client, tmp_path = client_and_tmp
    bus = _get_bus()
    pname = f"proj-sess-d-{id(bus)}"
    proj_dir = tmp_path / pname
    proj_dir.mkdir(parents=True, exist_ok=True)
    client.post(
        "/api/projects",
        json={"name": pname, "path": str(proj_dir)},
    )
    client.post(f"/api/projects/{pname}/active", json={})
    r = client.post("/api/sessions", json={"name": "sess-2", "project_name": pname})
    assert r.status_code == 200
    session_id = r.json()["session"]["id"]
    bus.publish.call_args_list.clear()

    r = client.delete(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    deleted = _published(bus, "session.deleted")
    assert len(deleted) == 1
    assert deleted[0]["id"] == session_id
    assert deleted[0]["project_name"] == pname


def test_set_active_session_publishes_active_session_changed(client_and_tmp) -> None:
    client, tmp_path = client_and_tmp
    bus = _get_bus()
    pname = f"proj-sess-a-{id(bus)}"
    proj_dir = tmp_path / pname
    proj_dir.mkdir(parents=True, exist_ok=True)
    client.post(
        "/api/projects",
        json={"name": pname, "path": str(proj_dir)},
    )
    client.post(f"/api/projects/{pname}/active", json={})
    r1 = client.post("/api/sessions", json={"name": "a", "project_name": pname})
    r2 = client.post("/api/sessions", json={"name": "b", "project_name": pname})
    assert r1.status_code == 200 and r2.status_code == 200
    sess_b = r2.json()["session"]["id"]
    bus.publish.call_args_list.clear()

    r = client.post(f"/api/sessions/{sess_b}/active", json={})
    assert r.status_code == 200
    changed = _published(bus, "active_session.changed")
    assert len(changed) == 1
    payload = changed[0]
    assert payload["id"] == sess_b
    assert payload["project_name"] == pname


# ---------------------------------------------------------------------------
# Negative: create_session without an active project still raises
# (and publishes nothing).
# ---------------------------------------------------------------------------


def test_create_session_without_active_project_publishes_nothing(client_and_tmp) -> None:
    client, _tmp_path = client_and_tmp
    bus = _get_bus()
    r = client.post("/api/sessions", json={"name": "x", "project_name": "nope"})
    assert r.status_code == 400
    created = _published(bus, "session.created")
    assert created == []
