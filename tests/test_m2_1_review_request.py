"""M2.1 step 4: review-request emission on the success branch.

When a delegation transitions to ``review``, the producer attaches a
``review_request`` (reviewer hint, diff pointer, manifest summary +
confidence when present) + a ``review_requested`` trace event. The
failure branch attaches nothing. ``promote`` keeps the request as
history (ruling 3).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
)


# The promote test drives a real background ``_run`` through the full
# app (runtime path, not the legacy stub) — it needs the
# ``SWEAVE_MOCK_OPENCODE=1`` seam or ``ServeRunner.start()`` spawns a
# real ``opencode serve`` (GOTCHAS: Writing runtime tests §1).
@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    import os

    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


def _legacy_runner(
    stores: PerProjectDelegationStores,
    succeed: bool,
    project_dir: Path,
    **kw,
):
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegationResult

    class _StubDelegateTool:
        async def execute(self, agent, task, model=None, task_id=None):
            if succeed:
                return DelegationResult(
                    success=True, agent=agent, task_id=task_id or "stub",
                    output="did the work", error=None,
                )
            return DelegationResult(
                success=False, agent=agent, task_id=task_id or "stub",
                output="", error="boom",
            )

    return JobRunner(
        delegate_tool=_StubDelegateTool(),  # type: ignore[arg-type]
        delegation_stores=stores,
        project_dir_resolver=lambda name: project_dir,
        turn_timeout=10.0,
        **kw,
    )


def _trace_events(project_dir: Path, delegation_id: str) -> list[dict]:
    from sweave.runtime.trace_log import TraceLog

    path = TraceLog(delegation_id, base_dir=project_dir).path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_success_to_review_carries_populated_request():
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-emit-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = _legacy_runner(stores, succeed=True, project_dir=project_dir)

    d = Delegation(
        agent="backend", task="t", project_name="p",
        worktree_path="/w", branch="feat-x", pr_url="http://pr/1",
        manifest={"intent": "add the endpoint", "confidence": 0.7},
    )
    await store.add(d)

    from sweave.runtime.trace_log import TraceLog

    await runner._run(d, TraceLog(d.delegation_id, base_dir=project_dir))

    rec = store.get(d.delegation_id)
    assert rec is not None
    assert rec.status == "review"
    assert rec.review_request is not None
    assert rec.review_request["reviewer_hint"] == "reviewer"
    assert rec.review_request["diff_ref"] == {
        "worktree_path": "/w", "branch": "feat-x", "pr_url": "http://pr/1",
    }
    assert rec.review_request["manifest_summary"] == "add the endpoint"
    assert rec.review_request["confidence"] == 0.7
    assert rec.review_request["requested_at"]

    events = _trace_events(project_dir, d.delegation_id)
    requested = [e for e in events if e.get("event") == "review_requested"]
    assert len(requested) == 1
    assert requested[0]["reviewer_hint"] == "reviewer"


@pytest.mark.asyncio
async def test_failed_attaches_no_request():
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-emit-fail-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = _legacy_runner(stores, succeed=False, project_dir=project_dir)

    d = Delegation(agent="backend", task="t", project_name="p")
    await store.add(d)

    from sweave.runtime.trace_log import TraceLog

    await runner._run(d, TraceLog(d.delegation_id, base_dir=project_dir))

    rec = store.get(d.delegation_id)
    assert rec is not None
    assert rec.status == "failed"
    assert rec.review_request is None
    events = _trace_events(project_dir, d.delegation_id)
    assert not [e for e in events if e.get("event") == "review_requested"]


def test_build_review_request_without_manifest():
    """No manifest on the record → summary/confidence None, diff
    pointer still populated from the delegation's own fields."""
    from sweave.runtime.job_runner import build_review_request

    d = Delegation(agent="a", task="t", worktree_path="/w", branch="b")
    req = build_review_request(d)
    assert req["reviewer_hint"] == "reviewer"
    assert req["diff_ref"] == {
        "worktree_path": "/w", "branch": "b", "pr_url": None,
    }
    assert req["manifest_summary"] is None
    assert req["confidence"] is None
    assert req["requested_at"]


def _build_state(monkeypatch, tmp_path: Path):
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


def test_promote_keeps_review_request_as_history(client: TestClient):
    """Ruling 3: promote flips review → done and leaves the request
    in place (audit trail, R6 signal)."""
    r = client.post("/api/v2/tasks", json={"task": "x", "agent": "backend"})
    assert r.status_code == 200, r.text
    did = r.json()["delegation_id"]

    # The background _run drives the stub to success → review +
    # request. Poll the record (the runner works async).
    import time

    deadline = time.monotonic() + 15.0
    rec = None
    while time.monotonic() < deadline:
        g = client.get(f"/api/delegations/{did}")
        assert g.status_code == 200
        rec = g.json()
        if rec["status"] == "review":
            break
        time.sleep(0.2)
    assert rec is not None and rec["status"] == "review", rec
    assert rec["review_request"] is not None
    assert rec["review_request"]["reviewer_hint"] == "reviewer"

    p = client.post(f"/api/delegations/{did}/promote")
    assert p.status_code == 200, p.text
    done = p.json()
    assert done["status"] == "done"
    assert done["review_request"] == rec["review_request"]
