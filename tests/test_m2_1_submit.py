"""M2.1 step 3: submit-path plumbing for ``blocking``.

``POST /api/v2/tasks`` accepts optional ``blocking`` (default false);
MCP ``defer`` accepts optional ``blocking`` and passes it through
(non-bool → ``rejected:`` line, same discipline as the estimate
non-dict guard); ``JobRunner.submit`` stores it. Rejected-chain
(loop/depth/budget) rules unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


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


def test_api_default_false(client: TestClient):
    """No blocking supplied → record carries False (ruling 1)."""
    r = client.post("/api/v2/tasks", json={"task": "x", "agent": "backend"})
    assert r.status_code == 200, r.text
    did = r.json()["delegation_id"]
    rec = client.get(f"/api/delegations/{did}")
    assert rec.status_code == 200
    assert rec.json()["blocking"] is False


def test_api_true_plumbed(client: TestClient):
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "blocking": True},
    )
    assert r.status_code == 200, r.text
    did = r.json()["delegation_id"]
    rec = client.get(f"/api/delegations/{did}")
    assert rec.status_code == 200
    assert rec.json()["blocking"] is True


@pytest.mark.asyncio
async def test_defer_passes_blocking_true(monkeypatch):
    captured: list[tuple[str, dict]] = []

    async def fake_post(path: str, body: dict, token: str) -> dict:
        captured.append((path, body))
        return {"delegation_id": "del-x", "status": "queued"}

    monkeypatch.setattr("sweave.mcp._http_post", fake_post)

    from mcp.types import CallToolRequestParams

    from sweave.mcp import _defer

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "backend",
            "task": "do it",
            "caller_delegation_id": "del-parent",
            "blocking": True,
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is False
    assert captured[0][1]["blocking"] is True


@pytest.mark.asyncio
async def test_defer_rejects_nonbool_blocking():
    from mcp.types import CallToolRequestParams

    from sweave.mcp import _defer

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "backend",
            "task": "do it",
            "caller_delegation_id": "del-parent",
            "blocking": "yes please",
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is True
    assert "rejected:" in result.content[0].text
    assert "blocking" in result.content[0].text


def test_defer_schema_advertises_blocking(monkeypatch):
    """The tool schema carries the optional blocking property so
    orchestrators know the slot exists."""
    import asyncio

    # Managed session: the provisioned env token (unprovisioned
    # servers list nothing — context-overhead gate).
    monkeypatch.setenv("SWEAVE_MCP_TOKEN", "test-token")
    from sweave.mcp import _list_tools_handler

    result = asyncio.run(_list_tools_handler(ctx=None, params=None))
    defer = next(t for t in result.tools if t.name == "defer")
    assert "blocking" in defer.input_schema["properties"]
    assert defer.input_schema["required"] == ["target", "task", "caller_delegation_id"]


@pytest.mark.asyncio
async def test_submit_stores_blocking(tmp_path: Path):
    """JobRunner.submit persists the flag on the record."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    stores = PerProjectDelegationStores()
    runner = JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),
        delegation_stores=stores,
    )
    d = await runner.submit(
        agent="backend", task="t", project_name="p", blocking=True,
    )
    assert d.blocking is True
    # Default path still False.
    d2 = await runner.submit(agent="backend", task="t2", project_name="p")
    assert d2.blocking is False
