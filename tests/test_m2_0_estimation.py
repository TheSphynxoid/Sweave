"""M2.0 estimation records (record-only) tests.

Every task delegation may carry ``estimate: {tokens, seconds} | None``
(supplied at submit, nullable, no behavior change); the detail
projection joins it against actuals (trace ``tokens_used`` summed +
created->completed wall seconds). No planner, no UI, no enforcement,
no calibration, no chat-turn estimates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sweave.runtime.delegation_store import Delegation


# ---------------------------------------------------------------------------
# App fixture (same stub pattern as test_m1_1_step4_api_bridge)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Schema: migration matrix + round-trip
# ---------------------------------------------------------------------------


def _minimal_record(version: int, **extra) -> dict:
    rec = {
        "schema_version": version,
        "delegation_id": "del-x",
        "task_id": "t1",
        "agent": "backend",
        "model": "",
        "task": "x",
        "status": "done",
        "created_at": "2026-09-10T00:00:00",
        "updated_at": "2026-09-10T00:00:00",
    }
    rec.update(extra)
    return rec


def test_v1_record_loads_as_v7_with_estimate_none():
    """Full chain: a v1 record passes through every migration
    (v1->v2->...->v8) and lands with estimate None.

    M2.1 step 2 update: schema is now v8 (blocking + review_request
    land on the same chain); the v1->v8 matrix lives in
    tests/test_m2_1_schema.py.
    M2.1-follow-up update: schema is now v9 (engine_session_id)."""
    d = Delegation.from_dict(_minimal_record(1))
    assert d.schema_version == 9
    assert d.estimate is None
    assert d.kind == "task"
    assert d.needs_attention is False
    assert d.archived is False
    assert d.blocking is False
    assert d.review_request is None
    assert d.engine_session_id is None


def test_v6_record_loads_as_v7_with_estimate_none():
    """M2.1 step 2 update: lands at v8 now (see above).
    M2.1-follow-up update: lands at v9 now."""
    d = Delegation.from_dict(_minimal_record(6))
    assert d.schema_version == 9
    assert d.estimate is None


def test_v7_estimate_round_trips():
    """M2.1 step 2 update: round-trips at v8 now (see above).
    M2.1-follow-up update: round-trips at v9 now."""
    d = Delegation(agent="a", task="t", estimate={"tokens": 1500, "seconds": 90.5})
    data = d.to_dict()
    assert data["estimate"] == {"tokens": 1500, "seconds": 90.5}
    back = Delegation.from_dict(data)
    assert back.estimate == {"tokens": 1500, "seconds": 90.5}
    assert back.schema_version == 9


def test_estimate_defaults_to_none():
    d = Delegation(agent="a", task="t")
    assert d.estimate is None
    assert d.to_dict()["estimate"] is None


# ---------------------------------------------------------------------------
# Submit plumbing: POST /api/v2/tasks
# ---------------------------------------------------------------------------


def test_v2_task_accepts_estimate(client: TestClient):
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend",
              "estimate": {"tokens": 2000, "seconds": 120}},
    )
    assert r.status_code == 200, r.text
    data = client.get(f"/api/delegations/{r.json()['delegation_id']}").json()
    assert data["estimate"] == {"tokens": 2000, "seconds": 120}


def test_v2_task_without_estimate_stores_none(client: TestClient):
    r = client.post("/api/v2/tasks", json={"task": "x", "agent": "backend"})
    assert r.status_code == 200, r.text
    data = client.get(f"/api/delegations/{r.json()['delegation_id']}").json()
    assert data["estimate"] is None


def test_v2_task_partial_estimate_round_trips(client: TestClient):
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "estimate": {"tokens": 500}},
    )
    assert r.status_code == 200, r.text
    data = client.get(f"/api/delegations/{r.json()['delegation_id']}").json()
    assert data["estimate"] == {"tokens": 500}


def test_v2_task_empty_estimate_normalises_to_none(client: TestClient):
    r = client.post(
        "/api/v2/tasks", json={"task": "x", "agent": "backend", "estimate": {}}
    )
    assert r.status_code == 200, r.text
    data = client.get(f"/api/delegations/{r.json()['delegation_id']}").json()
    assert data["estimate"] is None


def test_v2_task_negative_estimate_is_422(client: TestClient):
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "estimate": {"tokens": -5}},
    )
    assert r.status_code == 422


def test_v2_task_nonnumeric_estimate_is_422(client: TestClient):
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend", "estimate": {"seconds": "soon"}},
    )
    assert r.status_code == 422


def test_v2_task_estimate_ignores_unknown_keys(client: TestClient):
    """Lenient shape: LLM-supplied extras must not 422 the submit."""
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend",
              "estimate": {"tokens": 100, "confidence": "high"}},
    )
    assert r.status_code == 200, r.text
    data = client.get(f"/api/delegations/{r.json()['delegation_id']}").json()
    assert data["estimate"] == {"tokens": 100}


# ---------------------------------------------------------------------------
# MCP defer passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defer_passes_estimate_through(monkeypatch):
    captured: list[tuple[str, dict]] = []

    async def fake_post(path: str, body: dict, token: str) -> dict:
        captured.append((path, body))
        return {"delegation_id": "del-est", "status": "queued"}

    monkeypatch.setattr("sweave.mcp._http_post", fake_post)

    from sweave.mcp import _defer
    from mcp.types import CallToolRequestParams

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "backend",
            "task": "do it",
            "caller_delegation_id": "del-parent",
            "estimate": {"tokens": 700, "seconds": 45},
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is False
    assert captured[0][1]["estimate"] == {"tokens": 700, "seconds": 45}


@pytest.mark.asyncio
async def test_defer_without_estimate_sends_no_key(monkeypatch):
    captured: list[tuple[str, dict]] = []

    async def fake_post(path: str, body: dict, token: str) -> dict:
        captured.append((path, body))
        return {"delegation_id": "del-x", "status": "queued"}

    monkeypatch.setattr("sweave.mcp._http_post", fake_post)

    from sweave.mcp import _defer
    from mcp.types import CallToolRequestParams

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "backend",
            "task": "do it",
            "caller_delegation_id": "del-parent",
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is False
    assert "estimate" not in captured[0][1]


@pytest.mark.asyncio
async def test_defer_rejects_nondict_estimate():
    from sweave.mcp import _defer
    from mcp.types import CallToolRequestParams

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "backend",
            "task": "do it",
            "caller_delegation_id": "del-parent",
            "estimate": "a lot",
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is True
    assert "rejected:" in result.content[0].text
    assert "estimate" in result.content[0].text


def test_defer_schema_advertises_estimate():
    """The tool schema carries the optional estimate property so
    orchestrators know the slot exists (blocking is M2.1 — out)."""
    import asyncio

    from sweave.mcp import _list_tools_handler

    # Sync test: no loop running, so asyncio.run is safe.
    result = asyncio.run(_list_tools_handler(ctx=None, params=None))
    defer = next(t for t in result.tools if t.name == "defer")
    # (Snake case in Python; camelCase on the wire.)
    assert "estimate" in defer.input_schema["properties"]
    assert defer.input_schema["required"] == ["target", "task", "caller_delegation_id"]


# ---------------------------------------------------------------------------
# Projection: estimate-vs-actual in the detail view
# ---------------------------------------------------------------------------


def _write_trace(trace_path: Path, events: list[tuple[str, dict]]) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with open(trace_path, "w", encoding="utf-8") as f:
        for name, payload in events:
            f.write(json.dumps({"event": name, **payload}) + "\n")


def test_projection_with_estimate_and_trace(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    trace_dir = tmp_path / "traces"
    _write_trace(
        trace_dir / "d1.jsonl",
        [
            ("tokens_used", {"input": 10, "output": 5, "reasoning": 0,
                             "cache_read": 0, "cache_write": 0, "cost": 0.001}),
            ("tokens_used", {"input": 20, "output": 7, "reasoning": 3,
                             "cache_read": 1, "cache_write": 2, "cost": 0.002}),
        ],
    )
    detail = render_detail_view(
        "d1",
        trace_dir=trace_dir,
        estimate={"tokens": 100, "seconds": 60},
        created_at="2026-09-10T00:00:00",
        completed_at="2026-09-10T00:02:00",
    )
    eva = detail["estimate_vs_actual"]
    assert eva["estimate"] == {"tokens": 100, "seconds": 60}
    # Actual tokens SUM across turns (not the `tokens` section's
    # last-wins display).
    assert eva["actual"]["tokens"]["input"] == 30
    assert eva["actual"]["tokens"]["output"] == 12
    assert eva["actual"]["tokens"]["reasoning"] == 3
    assert eva["actual"]["tokens"]["cost"] == pytest.approx(0.003)
    assert eva["actual"]["seconds"] == 120.0


def test_projection_missing_trace_degrades_to_nulls(tmp_path: Path):
    """No trace file: actuals null, estimate still echoed, never a 500."""
    from sweave.web.detail_view import render_detail_view

    detail = render_detail_view(
        "ghost",
        trace_dir=tmp_path / "traces",
        estimate={"tokens": 50},
        created_at="2026-09-10T00:00:00",
        completed_at="2026-09-10T00:01:00",
    )
    eva = detail["estimate_vs_actual"]
    assert eva["estimate"] == {"tokens": 50}
    assert eva["actual"]["tokens"] is None
    assert eva["actual"]["seconds"] == 60.0


def test_projection_running_turn_has_null_seconds(tmp_path: Path):
    """Still-running delegation (no completed stamp): seconds null,
    tokens still project from the trace so far."""
    from sweave.web.detail_view import render_detail_view

    trace_dir = tmp_path / "traces"
    _write_trace(
        trace_dir / "d2.jsonl",
        [("tokens_used", {"input": 4, "output": 2})],
    )
    detail = render_detail_view(
        "d2",
        trace_dir=trace_dir,
        estimate=None,
        created_at="2026-09-10T00:00:00",
        completed_at=None,
    )
    eva = detail["estimate_vs_actual"]
    assert eva["estimate"] is None
    assert eva["actual"]["tokens"]["input"] == 4
    assert eva["actual"]["seconds"] is None


def test_projection_no_record_no_trace_is_all_null(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    detail = render_detail_view("nobody", trace_dir=tmp_path / "traces")
    eva = detail["estimate_vs_actual"]
    assert eva == {"estimate": None,
                   "actual": {"tokens": None, "seconds": None}}


def test_detail_endpoint_joins_record_estimate(client: TestClient):
    """GET .../detail carries estimate_vs_actual from the joined
    record (estimate) + trace (actual tokens)."""
    r = client.post(
        "/api/v2/tasks",
        json={"task": "x", "agent": "backend",
              "estimate": {"tokens": 300, "seconds": 30}},
    )
    assert r.status_code == 200, r.text
    did = r.json()["delegation_id"]
    detail = client.get(f"/api/delegations/{did}/detail")
    assert detail.status_code == 200
    eva = detail.json()["estimate_vs_actual"]
    assert eva["estimate"] == {"tokens": 300, "seconds": 30}
    # Fresh delegation: no trace yet, not completed.
    assert eva["actual"]["tokens"] is None
    assert eva["actual"]["seconds"] is None


def test_detail_endpoint_unknown_id_still_degrades(client: TestClient):
    """Unknown id keeps the old degrade contract (200 + nulls)."""
    r = client.get("/api/delegations/no-such-id/detail")
    assert r.status_code == 200
    assert r.json()["estimate_vs_actual"] == {
        "estimate": None, "actual": {"tokens": None, "seconds": None}
    }
