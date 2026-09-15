"""Usage-ledger projector + endpoint (``docs/USAGE_LEDGER_PLAN.md``).

* ``sweave.stats.ledger.build_summary`` is a pure fold over Delegation
  records + trace ``tokens_used`` events (derive, don't instrument:
  no new writes, no text collection).
* ``GET /api/stats/summary`` serves it (compute on read).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


def _rec(**kw):
    base = {
        "delegation_id": "d1",
        "agent": "backend",
        "model": "opencode/test-model",
        "status": "done",
        "kind": "task",
        "project_name": "demo",
        "error": None,
        "created_at": datetime(2026, 9, 14, 10, 0, 0),
        "completed_at": datetime(2026, 9, 14, 10, 5, 0),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _tokens(**kw):
    ev = {
        "event": "tokens_used",
        "input": 0, "output": 0, "reasoning": 0,
        "cache_read": 0, "cache_write": 0, "cost": 0,
    }
    ev.update(kw)
    return ev


def test_tokens_summed_per_delegation_missing_trace_is_zero():
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [_rec(delegation_id="a"), _rec(delegation_id="b", status="failed",
                                       error="[chat error: max_steps: nope]")],
        tokens_reader=lambda dep_id: (
            [_tokens(input=100, output=10, cache_read=40), _tokens(input=50)]
            if dep_id == "a" else []
        ),
        days=0,
    )
    assert out["totals"]["turns"] == 2
    assert out["totals"]["input"] == 150
    assert out["totals"]["output"] == 10
    assert out["totals"]["cache_read"] == 40
    assert out["totals"]["failed"] == 1
    assert out["by_status"] == {"done": 1, "failed": 1}
    assert out["by_error"] == [{"error": "max_steps", "count": 1}]


def test_context_input_is_peak_not_sum():
    """Billed ``input`` sums across turns; ``context_input`` maxes.

    Per-step prompts re-bill full history (steps×context), so the
    ledger keeps the billed sum on ``input`` and the peak live
    context on ``context_input`` (the fire-risk size). Pre-split
    traces without the field contribute 0 — never invented.
    """
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [_rec(delegation_id="a"), _rec(delegation_id="b")],
        tokens_reader=lambda dep_id: (
            [_tokens(input=100, context_input=100),
             _tokens(input=120, context_input=120)]
            if dep_id == "a"
            else [_tokens(input=50)]  # pre-split anchor: no peak recorded
        ),
        days=0,
    )
    assert out["totals"]["input"] == 270
    assert out["totals"]["context_input"] == 120
    assert out["by_agent"][0]["context_input"] == 120


def test_error_classes():
    from sweave.stats.ledger import error_class

    assert error_class("[chat error: max_steps: max loop iterations (50) exceeded]") == "max_steps"
    assert error_class("[chat error: TypeError: boom]") == "TypeError"
    assert error_class("some plain failure") == "unspecified"
    assert error_class("") is None
    assert error_class(None) is None
    assert error_class(42) is None


def test_day_window_and_dateless_records():
    from sweave.stats.ledger import build_summary

    now = datetime(2026, 9, 14, 12, 0, 0)
    recs = [
        _rec(delegation_id="today"),
        _rec(delegation_id="old", created_at=datetime(2026, 8, 1, 0, 0, 0),
             completed_at=datetime(2026, 8, 1, 0, 1, 0)),
        _rec(delegation_id="nodate", created_at=None, completed_at=None),
    ]
    out = build_summary(recs, tokens_reader=lambda _d: [], days=30, now=now)
    assert out["totals"]["turns"] == 2  # old excluded, dateless in totals
    assert [r["day"] for r in out["by_day"]] == ["2026-09-14"]
    assert out["by_day"][0]["turns"] == 1


def test_wall_seconds_completed_only():
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [_rec(delegation_id="a"),  # 300s
         _rec(delegation_id="b", status="running", completed_at=None)],
        tokens_reader=lambda _d: [],
        days=0,
    )
    assert out["totals"]["wall_seconds"] == 300.0
    assert out["totals"]["completed_turns"] == 1
    assert out["totals"]["turns"] == 2


def test_never_raises_on_garbage():
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [SimpleNamespace(), {"not": "a record"}, None],
        tokens_reader=lambda _d: (_ for _ in ()).throw(RuntimeError("boom")),
        days=0,
    )
    assert out["totals"]["turns"] == 3


def test_default_reader_scans_only_tokens_used(tmp_path: Path):
    from sweave.stats.ledger import default_tokens_reader

    trace = tmp_path / "dx.jsonl"
    trace.write_text(
        '{"event": "tool.started", "callID": "c1"}\n'
        'not json\n'
        '{"event": "tokens_used", "input": 7, "output": 3}\n',
        encoding="utf-8",
    )
    assert default_tokens_reader("dx", trace_dir=tmp_path) == [
        {"event": "tokens_used", "input": 7, "output": 3}
    ]
    assert default_tokens_reader("missing", trace_dir=tmp_path) == []


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path) -> "object":
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from sweave.web import state as state_mod

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):  # type: ignore[no-untyped-def]
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)

    from fastapi.testclient import TestClient

    from sweave.web.server import app

    with TestClient(app) as c:
        yield c


def _add_record(app_state, project_dir: Path, **kw):
    """Persist one Delegation record + optional trace into a project."""
    import asyncio

    from sweave.runtime.delegation_store import Delegation
    from sweave.runtime.trace_log import TraceLog

    rec = Delegation(
        agent=kw.get("agent", "backend"),
        model=kw.get("model", "opencode/m"),
        task="do it",
        project_name=kw.get("project_name", "stats-proj"),
        kind=kw.get("kind", "task"),
        status=kw.get("status", "done"),
        error=kw.get("error"),
    )
    store = asyncio.run(app_state.delegation_stores.for_project(project_dir))
    asyncio.run(store.add(rec))
    toks = kw.get("tokens")
    if toks:
        trace = TraceLog(rec.delegation_id)
        trace.append("tokens_used", dict(toks))
        trace.close()
    return rec


def test_stats_summary_endpoint_empty(client, tmp_path: Path):
    _ = tmp_path
    r = client.get("/api/stats/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("totals", "by_day", "by_model", "by_project",
                "by_agent", "by_kind", "by_status", "by_error"):
        assert key in body, key


def test_stats_summary_endpoint_with_records(client, tmp_path: Path):
    import tempfile

    from sweave.web.server import app

    proj_dir = Path(tempfile.mkdtemp())
    r = client.post("/api/projects", json={"name": "stats-proj", "path": str(proj_dir)})
    assert r.status_code == 200, r.text
    state = app.state.app_state
    _add_record(state, proj_dir, agent="backend",
                tokens={"input": 100, "output": 20, "cache_read": 30})
    _add_record(state, proj_dir, agent="reviewer", status="failed",
                error="[chat error: max_steps: exceeded]",
                tokens={"input": 50, "output": 5})

    r = client.get("/api/stats/summary", params={"days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["turns"] == 2
    assert body["totals"]["input"] == 150
    assert body["totals"]["cache_read"] == 30
    assert body["totals"]["failed"] == 1
    assert body["by_error"] == [{"error": "max_steps", "count": 1}]
    assert {row["agent"] for row in body["by_agent"]} == {"backend", "reviewer"}

    r = client.get("/api/stats/summary", params={"days": 0})
    assert r.status_code == 422
