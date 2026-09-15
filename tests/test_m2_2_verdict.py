"""M2.2 verdict payload: reviewer judgments on review-status delegations.

`POST /api/delegations/{id}/verdict` records an advisory verdict
(approve | request_changes + comments); the detail projection folds
it in for reviewer visibility. A verdict never changes status,
never clears ``needs_attention``, never promotes — the
human-promotes ruling stands (R2 automates via the promote
endpoint later). Schema v11→v13 (``verdict`` None by default).
"""

from __future__ import annotations

import tempfile
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
    SCHEMA_VERSION,
)


def _endpoint_state(tmp_path: Path, stores, project_dir: Path):
    from sweave.runtime.escalation import EscalationStore

    return types.SimpleNamespace(
        delegation_stores=stores,
        escalation_store=EscalationStore(base_dir=tmp_path / "esc"),
        event_bus=None,
        traces_dir=tmp_path / "traces",
    )


def _trace_events(base_dir: Path, delegation_id: str) -> list[dict]:
    import json

    from sweave.runtime.trace_log import TraceLog

    path = TraceLog(delegation_id, base_dir=base_dir).path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _review_delegation(tmp_path: Path, **kw):
    stores = PerProjectDelegationStores()
    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m22-verdict-"))
    store = await stores.for_project(project_dir)
    kw.setdefault("status", "review")
    d = Delegation(agent="backend", task="t", project_name="p", **kw)
    await store.add(d)
    state = _endpoint_state(tmp_path, stores, project_dir)
    return stores, store, state, d


def test_schema_v13_verdict_defaults_none():
    assert SCHEMA_VERSION == 13
    d = Delegation(agent="a", task="t")
    assert d.verdict is None
    assert d.to_dict()["verdict"] is None
    assert d.fix_of is None
    assert d.fix_round == 0


def test_v11_record_loads_as_v13_with_verdict_none():
    rec = {
        "schema_version": 11,
        "delegation_id": "del-v",
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
    assert d.verdict is None
    assert d.worktree_owned is True


def test_verdict_round_trip():
    d = Delegation(
        agent="a",
        task="t",
        verdict={
            "decision": "request_changes",
            "comments": "fix the null check",
            "confidence": 0.9,
            "reviewer": "reviewer",
            "decided_at": "2026-09-15T00:00:00",
            "gotcha_hits": [],
            "output_claims_checked": False,
        },
    )
    back = Delegation.from_dict(d.to_dict())
    assert back.verdict is not None
    assert back.verdict["decision"] == "request_changes"
    assert back.verdict["comments"] == "fix the null check"
    assert back.schema_version == 13


@pytest.mark.asyncio
async def test_approve_verdict_recorded_status_kept(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    _, store, state, d = await _review_delegation(
        tmp_path, needs_attention=True
    )
    out = await record_verdict(
        d.delegation_id,
        VerdictIn(decision="approve", reviewer="human", confidence=0.8),
        state,  # type: ignore[arg-type]
    )
    assert out["status"] == "review"
    assert out["verdict"]["decision"] == "approve"
    assert out["verdict"]["reviewer"] == "human"
    assert out["verdict"]["confidence"] == 0.8
    assert out["verdict"]["decided_at"]
    assert out["verdict"]["gotcha_hits"] == []
    assert out["verdict"]["output_claims_checked"] is False
    # Advisory: flag untouched, still owes promotion.
    assert store.get(d.delegation_id).needs_attention is True
    events = _trace_events(state.traces_dir, d.delegation_id)
    recorded = [e for e in events if e.get("event") == "verdict_recorded"]
    assert len(recorded) == 1
    assert recorded[0]["decision"] == "approve"


@pytest.mark.asyncio
async def test_request_changes_needs_comments(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    _, _, state, d = await _review_delegation(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await record_verdict(
            d.delegation_id,
            VerdictIn(decision="request_changes", comments="   "),
            state,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_verdict_rejected_off_review(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    _, _, state, d = await _review_delegation(tmp_path, status="done")
    with pytest.raises(HTTPException) as exc:
        await record_verdict(
            d.delegation_id, VerdictIn(decision="approve"), state  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_verdict_unknown_id_404(tmp_path: Path):
    from sweave.web.routers.delegations import VerdictIn, record_verdict

    _, _, state, _ = await _review_delegation(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await record_verdict(
            "no-such-id", VerdictIn(decision="approve"), state  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_promote_keeps_verdict_as_history(tmp_path: Path):
    """Promote preserves the verdict (M2.1 ruling-3 pattern)."""
    from sweave.web.routers.delegations import (
        VerdictIn,
        promote_delegation,
        record_verdict,
    )

    _, store, state, d = await _review_delegation(
        tmp_path, needs_attention=True
    )
    await record_verdict(
        d.delegation_id,
        VerdictIn(decision="approve", comments="lgtm", reviewer="human"),
        state,  # type: ignore[arg-type]
    )
    done = await promote_delegation(d.delegation_id, state)  # type: ignore[arg-type]
    assert done["status"] == "done"
    assert done["verdict"] is not None
    assert done["verdict"]["decision"] == "approve"
    assert store.get(d.delegation_id).needs_attention is False


def test_detail_fold_carries_verdict(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    verdict = {
        "decision": "request_changes",
        "comments": "fix it",
        "confidence": None,
        "reviewer": "reviewer",
        "decided_at": "2026-09-15T00:00:00",
        "gotcha_hits": [],
        "output_claims_checked": False,
    }
    detail = render_detail_view(
        "d1", trace_dir=tmp_path / "traces", verdict=verdict
    )
    assert detail["verdict"] == verdict


def test_detail_fold_degrades_without_verdict(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    detail = render_detail_view("ghost", trace_dir=tmp_path / "traces")
    assert detail["verdict"] is None
