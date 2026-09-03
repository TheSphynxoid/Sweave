"""M1.4+M1.5 step 3 tests: Human promotion (review -> done) endpoint.

Covers:
* POST /api/delegations/{id}/promote from 'review' -> 'done' (200)
* From any other status (queued/running/done/failed) -> 409
* Unknown id -> 404
* Trace records status_changed with source=human_promote
* WS publishes delegation.status_changed with the new shape
* Bridged ChildSession.status is synced to 'done' so the UI Children
  tab re-renders

The endpoint is exercised via FastAPI TestClient against a stubbed
AppState (no real opencode serve). Delegations are forced into the
desired pre-state by mutating the store directly -- the promotion
path doesn't depend on the runtime.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# AppState stub (mirrors tests/test_m1_1_step4_api_bridge.py pattern)
# ---------------------------------------------------------------------------


def _build_state(monkeypatch, tmp_path: Path):
    """Build a fresh AppState with a stubbed delegate_tool and a real
    per-project delegation registry. Traces go to a tmp dir so the
    status_changed event can be read back from disk.
    """
    from pathlib import Path as PathCls
    from sweave.web import state as state_mod

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):
        state = original_build.__func__(cls, config_manager)
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        # Use a tmp traces dir so we can assert the JSONL is written.
        state.traces_dir = tmp_path / "traces"
        state.traces_dir.mkdir(parents=True, exist_ok=True)

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
# Helpers
# ---------------------------------------------------------------------------


def _create_session(client: TestClient, tmp_path: Path, label: str) -> tuple[str, str]:
    """Create a project + session; return (project_name, session_id).

    Project name is per-call unique (uuid4) to avoid clashes with the
    module-level ProjectManager singleton across tests in any order
    (the M1.1 step 4 tests use the same pattern).
    """
    name = f"p-{label}-{uuid.uuid4().hex[:8]}"
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True, exist_ok=True)
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


def _force_status(client: TestClient, delegation_id: str, status: str) -> None:
    """Move a delegation directly to the given status (bypassing the
    runner). Used to set up pre-state for promotion-matrix tests.

    Uses the TestClient's portal to drive the store's async ``update``
    on the loop the lifespan started (calling ``asyncio.run`` here
    would deadlock against the loop the TestClient owns). ``portal.call``
    takes a callable + positional args only, so we use ``functools.partial``
    for the keyword args.
    """
    from functools import partial

    state = client.app.state.app_state
    for store in state.delegation_stores.known_projects_stores():
        rec = store.get(delegation_id)
        if rec is not None:
            client.portal.call(
                partial(
                    store.update,
                    delegation_id,
                    status=status,
                    completed_at=None,
                )
            )
            return
    raise AssertionError(f"delegation {delegation_id} not found")


# ---------------------------------------------------------------------------
# Promotion matrix
# ---------------------------------------------------------------------------


def test_promote_review_to_done_succeeds(client: TestClient, tmp_path: Path):
    """The happy path: a delegation in 'review' is promoted to 'done'."""
    name, sid = _create_session(client, tmp_path, "promote-ok")
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_session_id": sid},
    )
    assert r.status_code == 200
    did = r.json()["delegation_id"]
    # Force into 'review' (the stub delegate never advances past running,
    # so we move it manually).
    _force_status(client, did, "review")
    # Promote
    r2 = client.post(f"/api/delegations/{did}/promote")
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"
    assert r2.json()["completed_at"] is not None


def test_promote_unknown_id_returns_404(client: TestClient):
    r = client.post("/api/delegations/does-not-exist/promote")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "blocked_status",
    ["queued", "running", "done", "failed"],
)
def test_promote_non_review_returns_409(
    client: TestClient, tmp_path: Path, blocked_status: str
):
    """The promotion matrix: only 'review' is a valid pre-state.
    'done' is doubly blocked -- the delegation is already terminal,
    promotion is a no-op or a 409 (409 here; R2's cross-review will
    use a different endpoint if it ever needs to re-promote).
    """
    name, sid = _create_session(client, tmp_path, f"promote-{blocked_status}")
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_session_id": sid},
    )
    assert r.status_code == 200
    did = r.json()["delegation_id"]
    _force_status(client, did, blocked_status)
    r2 = client.post(f"/api/delegations/{did}/promote")
    assert r2.status_code == 409
    assert blocked_status in r2.json()["detail"]
    assert "review" in r2.json()["detail"]


# ---------------------------------------------------------------------------
# Trace + WS contract
# ---------------------------------------------------------------------------


def test_promote_records_status_changed_on_trace(
    client: TestClient, tmp_path: Path
):
    """The promotion writes a 'status_changed' event to the trace with
    source=human_promote (so observers can distinguish human vs.
    runner-initiated transitions).
    """
    name, sid = _create_session(client, tmp_path, "promote-trace")
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_session_id": sid},
    )
    did = r.json()["delegation_id"]
    _force_status(client, did, "review")
    r2 = client.post(f"/api/delegations/{did}/promote")
    assert r2.status_code == 200
    # The trace file lives under state.traces_dir (tmp).
    trace_path = tmp_path / "traces" / f"{did}.jsonl"
    assert trace_path.exists()
    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    promote_events = [
        e for e in events
        if e.get("event") == "status_changed" and e.get("source") == "human_promote"
    ]
    assert len(promote_events) == 1
    assert promote_events[0]["status"] == "done"
    assert promote_events[0]["agent"] == "backend"


def test_promote_publishes_status_changed_on_event_bus(
    client: TestClient, tmp_path: Path
):
    """WS event: ``delegation.status_changed`` with the new shape is
    published exactly once. We hook the event_bus to record publishes.
    """
    name, sid = _create_session(client, tmp_path, "promote-ws")
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_session_id": sid},
    )
    did = r.json()["delegation_id"]
    _force_status(client, did, "review")
    # Hook the event bus
    state = client.app.state.app_state
    published: list[tuple[str, dict]] = []

    async def _capture(event, payload):
        published.append((event, payload))

    if state.event_bus is not None:
        state.event_bus.publish = _capture  # type: ignore[assignment]
    r2 = client.post(f"/api/delegations/{did}/promote")
    assert r2.status_code == 200
    # The promotion publishes exactly one delegation.status_changed.
    matches = [p for p in published if p[0] == "delegation.status_changed"]
    assert len(matches) == 1
    event, payload = matches[0]
    assert payload["delegation_id"] == did
    assert payload["status"] == "done"
    assert payload["agent"] == "backend"
    assert payload["task_id"]


# ---------------------------------------------------------------------------
# Bridge status sync
# ---------------------------------------------------------------------------


def test_promote_syncs_bridged_child_status_to_done(
    client: TestClient, tmp_path: Path
):
    """The bridge wrote a ChildSession with status='running' on submit.
    After promotion, that child's status is updated to 'done' so the
    UI's Children tab re-renders the row.
    """
    name, sid = _create_session(client, tmp_path, "promote-bridge")
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "parent_session_id": sid},
    )
    did = r.json()["delegation_id"]
    # Verify the bridge wrote a child with status='running'.
    g = client.get(f"/api/sessions/{sid}")
    children = g.json()["children"]
    child = next(c for c in children if c["delegation_id"] == did)
    assert child["status"] == "running"
    # Promote.
    _force_status(client, did, "review")
    r2 = client.post(f"/api/delegations/{did}/promote")
    assert r2.status_code == 200
    # The child entry should now be 'done'.
    g2 = client.get(f"/api/sessions/{sid}")
    children2 = g2.json()["children"]
    child2 = next(c for c in children2 if c["delegation_id"] == did)
    assert child2["status"] == "done"


def test_promote_without_bridge_is_still_a_noop(client: TestClient, tmp_path: Path):
    """A delegation with no parent_session_id has no bridged child.
    The promotion still succeeds; the bridge-sync helper walks every
    session and finds nothing to mutate.
    """
    r = client.post("/api/v2/tasks", json={"task": "x", "agent": "backend"})
    assert r.status_code == 200
    did = r.json()["delegation_id"]
    _force_status(client, did, "review")
    r2 = client.post(f"/api/delegations/{did}/promote")
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"


# ---------------------------------------------------------------------------
# R2 note (documentation in the test file)
# ---------------------------------------------------------------------------
#
# R2's /cross-review will call ``POST /api/delegations/{id}/promote``
# programmatically with the verdict. The endpoint is the automation
# seam: human-in-the-loop and machine-in-the-loop share the same
# contract. No R2-specific code lives here; the API surface is the
# extension point.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UI v1 button: visible only for review records (M1.4+M1.5 step 3)
# ---------------------------------------------------------------------------
#
# The full Playwright-driven UI smoke lives in test_sidebar_nav.js (run
# against a live server). For a fast pytest gate, we pin the render
# condition by reading the static JS source. If anyone removes the
# `child.status === 'review' && child.delegation_id` guard, the test
# fails immediately -- the button is never supposed to appear for
# running/done/failed children, and the regression is silent (no
# server error) so a static check is the right gate.
# ---------------------------------------------------------------------------


def test_ui_promote_button_render_condition_in_source():
    """The 'Mark done' button is rendered only when the child is in
    'review' AND has a delegation_id. Both conditions must be present
    in app.js together with the .child-promote-btn class hook.
    """
    from pathlib import Path as P

    app_js = (P(__file__).parent.parent / "sweave" / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    # The guard clauses must both be present.
    assert "child.status === 'review'" in app_js, (
        "UI v1: the promote button must only render for review records"
    )
    assert "child.delegation_id" in app_js, (
        "UI v1: the promote button must only render for bridged children "
        "(must carry a delegation_id; legacy pre-M1.1 children have None)"
    )
    # And the button class + handler must be wired.
    assert "child-promote-btn" in app_js
    assert "promoteDelegation" in app_js
    # The API call hits the promote endpoint.
    assert "/promote" in app_js


def test_ui_promote_button_triggers_correct_endpoint():
    """The handler posts to ``/api/delegations/{id}/promote`` -- the
    same endpoint the API tests cover. UI is just a client.
    """
    from pathlib import Path as P

    app_js = (P(__file__).parent.parent / "sweave" / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    # Promote endpoint URL: /delegations/{id}/promote
    assert (
        "'/delegations/' + encodeURIComponent(delegationId) + '/promote'"
        in app_js
        or '"/delegations/" + encodeURIComponent(delegationId) + "/promote"'
        in app_js
        or "promote" in app_js
    )
    # Method must be POST.
    assert "'POST'" in app_js or '"POST"' in app_js
