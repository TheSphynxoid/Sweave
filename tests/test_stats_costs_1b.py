"""USAGE_LEDGER Phase 1b: ledger cost cells (compute-on-read, shared helper).

Backend cells gain ``estimated_cost`` + ``cost_source`` + ``unpriced``
via ``sweave/stats/pricing.py``. Degrade rule: unpriced turns bump the
flag, never $0. Midnight/retry pins: ``created_at``-day attribution,
retries linked by parent not merged.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace


def _rec(**kw):
    base = {
        "delegation_id": "d1",
        "agent": "backend",
        "model": "prov/m",
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
    ev = {"event": "tokens_used", "input": 0, "output": 0}
    ev.update(kw)
    return ev


META = {"prov/m": {"cost": {"input": 1.0, "output": 4.0}}}


def test_cells_price_via_rates():
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [_rec(delegation_id="a")],
        tokens_reader=lambda _d: [_tokens(input=1_000_000, output=500_000)],
        meta_reader=lambda m: META,
        days=0,
    )
    assert out["totals"]["estimated_cost"] == 3.0
    assert out["totals"]["cost_source"] == "rates"
    assert out["totals"]["unpriced"] is False
    assert out["by_day"][0]["estimated_cost"] == 3.0


def test_provider_cost_wins_zero_with_tokens_is_free():
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [_rec(delegation_id="a")],
        tokens_reader=lambda _d: [_tokens(input=10, output=5, cost=0)],
        meta_reader=lambda m: META,
        days=0,
    )
    assert out["totals"]["cost_source"] == "provider"
    assert out["totals"]["estimated_cost"] == 0.0
    assert out["totals"]["unpriced"] is False


def test_unpriced_never_zero():
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [_rec(delegation_id="a", model="unknown/m")],
        tokens_reader=lambda _d: [_tokens(input=10, output=5)],
        meta_reader=lambda m: META,
        days=0,
    )
    assert out["totals"]["estimated_cost"] == 0.0  # nothing priced
    assert out["totals"]["unpriced"] is True
    assert out["totals"]["cost_source"] == "none"


def test_counts_only_without_meta_reader():
    from sweave.stats.ledger import build_summary

    out = build_summary(
        [_rec(delegation_id="a")],
        tokens_reader=lambda _d: [_tokens(input=10, output=5)],
        days=0,
    )
    assert out["totals"]["estimated_cost"] == 0.0
    assert out["totals"]["unpriced"] is True


def test_midnight_attributes_created_at_day():
    from sweave.stats.ledger import build_summary

    rec = _rec(
        delegation_id="a",
        created_at=datetime(2026, 9, 14, 23, 59, 0),
        completed_at=datetime(2026, 9, 15, 0, 5, 0),
    )
    out = build_summary(
        [rec],
        tokens_reader=lambda _d: [_tokens(input=1_000_000, output=0)],
        meta_reader=lambda m: META,
        days=30,
        now=datetime(2026, 9, 15, 12, 0, 0),
    )
    assert [r["day"] for r in out["by_day"]] == ["2026-09-14"]
    assert out["by_day"][0]["estimated_cost"] == 1.0


def test_retries_linked_by_parent_not_merged():
    from sweave.stats.ledger import build_summary

    parent = _rec(delegation_id="p")
    retry = _rec(delegation_id="r")
    retry.parent_task_id = "p"
    out = build_summary(
        [parent, retry],
        tokens_reader=lambda _d: [_tokens(input=1_000_000, output=0)],
        meta_reader=lambda m: META,
        days=0,
    )
    assert out["totals"]["turns"] == 2
    assert out["totals"]["estimated_cost"] == 2.0


def test_summary_endpoint_carries_cost_fields():
    import asyncio
    from pathlib import Path

    from fastapi.testclient import TestClient

    from sweave.runtime.delegation_store import Delegation
    from sweave.runtime.trace_log import TraceLog
    from sweave.web.server import app

    proj_dir = Path(__import__("tempfile").mkdtemp())
    with TestClient(app) as c:
        r = c.post("/api/projects", json={"name": "cost-proj", "path": str(proj_dir)})
        assert r.status_code == 200, r.text
        state = app.state.app_state
        store = asyncio.run(state.delegation_stores.for_project(proj_dir))
        rec = Delegation(agent="backend", model="prov/m", task="t",
                         project_name="cost-proj", status="done")
        asyncio.run(store.add(rec))
        trace = TraceLog(rec.delegation_id)
        trace.append("tokens_used", {"input": 1_000_000, "output": 0})
        trace.close()
        r = c.get("/api/stats/summary", params={"days": 30})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["totals"]["unpriced"] is True  # no meta wired server-side
        assert "estimated_cost" in body["totals"]
        assert "cost_source" in body["totals"]
