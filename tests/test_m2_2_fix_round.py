"""M2.2 follow-up: fix rounds spawned from request_changes verdicts.

A ``request_changes`` verdict resolves the fix per the
``review_fix_mode`` routing toggle (user setting, never an LLM
parameter): ``direct`` spawns the fix child immediately;
``supervised`` records a proposal the human spawns via the
fix-round endpoint. The ``review_fix_max_rounds`` bound + the
double-spawn guard apply on both paths; judgment is never blocked,
only the auto-retry. Schema v12→v13 (``fix_of`` + ``fix_round``).
"""

from __future__ import annotations

import tempfile
import types
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
    SCHEMA_VERSION,
)


def _endpoint_state(tmp_path: Path, stores, project_dir: Path, **kw):
    from sweave.runtime.escalation import EscalationStore

    return types.SimpleNamespace(
        delegation_stores=stores,
        escalation_store=EscalationStore(base_dir=tmp_path / "esc"),
        event_bus=None,
        traces_dir=tmp_path / "traces",
        config_manager=kw.get("config_manager"),
        job_runner=kw.get("job_runner"),
    )


class _StubConfigManager:
    """Minimal routing surface (global only unless overlay given)."""

    def __init__(self, mode="direct", max_rounds=2):
        self._mode = mode
        self._max = max_rounds

    def get_routing(self):
        return types.SimpleNamespace(
            review_fix_mode=self._mode,
            review_fix_max_rounds=self._max,
        )

    def get_routing_for_project(self, _project_dir):
        return self.get_routing()


class _StubRunner:
    """JobRunner.submit double: records kwargs, files the child in
    the same project store (so the double-spawn guard sees it)."""

    def __init__(self, store):
        self.store = store
        self.submitted: list[dict] = []

    async def submit(self, agent, task, **kw):
        self.submitted.append({"agent": agent, "task": task, **kw})
        d = Delegation(
            agent=agent,
            task=task,
            project_name=kw.get("project_name"),
            parent_session_id=kw.get("parent_session_id"),
            parent_task_id=kw.get("parent_task_id"),
            depth=kw.get("depth", 0),
            chain_root_id=kw.get("chain_root_id"),
            blocking=kw.get("blocking", False),
            fix_of=kw.get("fix_of"),
            fix_round=kw.get("fix_round", 0),
        )
        await self.store.add(d)
        return d


async def _review_state(tmp_path: Path, mode="direct", max_rounds=2, **rec_kw):
    stores = PerProjectDelegationStores()
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m22-fixrev-"))
    store = await stores.for_project(project_dir)
    rec_kw.setdefault("status", "review")
    d = Delegation(agent="backend", task="build it", project_name="p", **rec_kw)
    await store.add(d)
    runner = _StubRunner(store)
    state = _endpoint_state(
        tmp_path, stores, project_dir,
        config_manager=_StubConfigManager(mode, max_rounds),
        job_runner=runner,
    )
    return stores, store, state, runner, d


def test_schema_v13_fix_lineage_defaults():
    assert SCHEMA_VERSION == 13
    d = Delegation(agent="a", task="t")
    assert d.fix_of is None
    assert d.fix_round == 0
    assert d.to_dict()["fix_of"] is None
    assert d.to_dict()["fix_round"] == 0


def test_v12_record_loads_as_v13():
    rec = {
        "schema_version": 12,
        "delegation_id": "del-f",
        "task_id": "t1",
        "agent": "backend",
        "model": "",
        "task": "x",
        "status": "review",
        "created_at": "2026-09-10T00:00:00",
        "updated_at": "2026-09-10T00:00:00",
    }
    d = Delegation.from_dict(rec)
    assert d.schema_version == 13
    assert d.fix_of is None
    assert d.fix_round == 0
    assert d.verdict is None


def test_review_fix_config_defaults_and_validation():
    from sweave.config.schemas import RoutingConfig

    r = RoutingConfig()
    assert r.review_fix_mode == "direct"
    assert r.review_fix_max_rounds == 2
    with pytest.raises(Exception):
        RoutingConfig(review_fix_mode="auto")
    with pytest.raises(Exception):
        RoutingConfig(review_fix_max_rounds=-1)
    with pytest.raises(Exception):
        RoutingConfig(review_fix_max_rounds=11)


@pytest.mark.asyncio
async def test_direct_mode_spawns_fix_child(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    stores, store, state, runner, d = await _review_state(tmp_path, mode="direct")
    out = await record_verdict(
        d.delegation_id,
        VerdictIn(decision="request_changes", comments="fix the null check",
                  reviewer="reviewer"),
        state,  # type: ignore[arg-type]
    )
    assert out["status"] == "review"
    assert out["fix_rejected"] is None
    assert out["fix_proposed"] is None
    spawned = out["fix_round_spawned"]
    assert spawned is not None
    assert spawned["agent"] == "backend"
    assert spawned["fix_round"] == 1
    assert len(runner.submitted) == 1
    sub = runner.submitted[0]
    assert sub["fix_of"] == d.delegation_id
    assert sub["fix_round"] == 1
    assert sub["parent_task_id"] == d.delegation_id
    assert "fix the null check" in sub["task"]
    assert "[fix-round 1" in sub["task"]


@pytest.mark.asyncio
async def test_fix_assignee_override_wins(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    stores, store, state, runner, d = await _review_state(tmp_path, mode="direct")
    out = await record_verdict(
        d.delegation_id,
        VerdictIn(decision="request_changes", comments="needs frontend eyes",
                  fix_assignee="frontend"),
        state,  # type: ignore[arg-type]
    )
    assert out["fix_round_spawned"]["agent"] == "frontend"
    assert store.get(d.delegation_id).verdict["fix_assignee"] == "frontend"


@pytest.mark.asyncio
async def test_supervised_mode_proposes_only(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    stores, store, state, runner, d = await _review_state(tmp_path, mode="supervised")
    out = await record_verdict(
        d.delegation_id,
        VerdictIn(decision="request_changes", comments="fix it"),
        state,  # type: ignore[arg-type]
    )
    assert out["fix_round_spawned"] is None
    assert out["fix_rejected"] is None
    assert out["fix_proposed"] == {"agent": "backend", "fix_round": 1}
    assert runner.submitted == []


@pytest.mark.asyncio
async def test_fix_endpoint_spawns_in_supervised(tmp_path: Path):
    from sweave.web.routers.delegations import (
        FixRoundIn,
        VerdictIn,
        record_verdict,
        spawn_fix_round,
    )

    stores, store, state, runner, d = await _review_state(tmp_path, mode="supervised")
    await record_verdict(
        d.delegation_id, VerdictIn(decision="request_changes", comments="fix it"),
        state,  # type: ignore[arg-type]
    )
    child = await spawn_fix_round(
        d.delegation_id, FixRoundIn(assignee="frontend"), state  # type: ignore[arg-type]
    )
    assert child["fix_of"] == d.delegation_id
    assert child["fix_round"] == 1
    assert child["agent"] == "frontend"
    assert len(runner.submitted) == 1


@pytest.mark.asyncio
async def test_fix_endpoint_rejects_without_verdict(tmp_path: Path):
    from fastapi import HTTPException

    from sweave.web.routers.delegations import FixRoundIn, spawn_fix_round

    stores, store, state, runner, d = await _review_state(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await spawn_fix_round(
            d.delegation_id, FixRoundIn(), state  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_max_rounds_bound_blocks_auto_but_keeps_verdict(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    stores, store, state, runner, d = await _review_state(
        tmp_path, mode="direct", max_rounds=1, fix_round=1, fix_of="orig-1"
    )
    out = await record_verdict(
        d.delegation_id,
        VerdictIn(decision="request_changes", comments="still broken"),
        state,  # type: ignore[arg-type]
    )
    # Judgment recorded; auto-retry refused.
    assert out["verdict"]["decision"] == "request_changes"
    assert out["fix_round_spawned"] is None
    assert "max_rounds" in (out["fix_rejected"] or "")
    assert runner.submitted == []


@pytest.mark.asyncio
async def test_double_spawn_guard(tmp_path: Path):
    from fastapi import HTTPException

    from sweave.web.routers.delegations import (
        FixRoundIn,
        VerdictIn,
        record_verdict,
        spawn_fix_round,
    )

    stores, store, state, runner, d = await _review_state(tmp_path, mode="supervised")
    await record_verdict(
        d.delegation_id, VerdictIn(decision="request_changes", comments="fix it"),
        state,  # type: ignore[arg-type]
    )
    await spawn_fix_round(d.delegation_id, FixRoundIn(), state)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        await spawn_fix_round(d.delegation_id, FixRoundIn(), state)  # type: ignore[arg-type]
    assert exc.value.status_code == 409
    assert "already spawned" in exc.value.detail


@pytest.mark.asyncio
async def test_approve_spawns_nothing(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    stores, store, state, runner, d = await _review_state(tmp_path, mode="direct")
    out = await record_verdict(
        d.delegation_id, VerdictIn(decision="approve", comments="lgtm"),
        state,  # type: ignore[arg-type]
    )
    assert out["fix_round_spawned"] is None
    assert out["fix_proposed"] is None
    assert out["fix_rejected"] is None
    assert runner.submitted == []


def test_detail_fold_carries_fix_rounds(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    detail = render_detail_view(
        "d1",
        trace_dir=tmp_path / "traces",
        fix_rounds=[{"delegation_id": "c1", "agent": "backend",
                     "status": "review", "fix_round": 1}],
    )
    assert detail["fix_rounds"] == [{"delegation_id": "c1", "agent": "backend",
                                     "status": "review", "fix_round": 1}]


def test_detail_fold_fix_rounds_default_empty(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    detail = render_detail_view("ghost", trace_dir=tmp_path / "traces")
    assert detail["fix_rounds"] == []
