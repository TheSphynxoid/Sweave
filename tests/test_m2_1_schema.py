"""M2.1 step 2: schema v7→v8 (blocking + review_request).

Every task delegation may carry ``blocking: bool`` (default False,
supplied at submit) and, once finished, an optional ``review_request``
record (embedded TypedDict on the Manifest/Estimate precedent).
"""

from __future__ import annotations

from sweave.runtime.delegation_store import Delegation, SCHEMA_VERSION


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


def test_v1_record_loads_as_v11_with_waitset_defaults():
    """Full chain: a v1 record passes through every migration
    (v1…v10…v11) and lands with blocking False + review_request None.

    M2.1-follow-up update: lands at v9 now (+ engine_session_id None).
    Review Phase 1 update: lands at v10 (+ review_bundle None).
    Worktree-policy update: lands at v11 (+ worktree_owned True).
    M2.2 update: lands at v12 (+ verdict None).
    M2.2 follow-up update: lands at v13 (+ fix_of None, fix_round 0)."""
    d = Delegation.from_dict(_minimal_record(1))
    assert d.schema_version == SCHEMA_VERSION == 13
    assert d.blocking is False
    assert d.review_request is None
    assert d.engine_session_id is None
    assert d.review_bundle is None
    assert d.worktree_owned is True
    assert d.verdict is None
    assert d.fix_of is None
    assert d.fix_round == 0
    # Earlier migrations still hold.
    assert d.estimate is None
    assert d.kind == "task"
    assert d.needs_attention is False
    assert d.archived is False


def test_v7_record_loads_as_v8_with_waitset_defaults():
    """Pre-M2.1 review records load with review_request None (a v7
    record in status review carries no request — step 4 attaches
    requests only to new transitions).

    M2.1-follow-up update: lands at v9 now (+ engine_session_id None).
    Review Phase 1 update: lands at v10 (+ review_bundle None).
    Worktree-policy update: lands at v11 (+ worktree_owned True).
    M2.2 update: lands at v12 (+ verdict None).
    M2.2 follow-up update: lands at v13 (+ fix lineage)."""
    d = Delegation.from_dict(_minimal_record(7, status="review"))
    assert d.schema_version == 13
    assert d.blocking is False
    assert d.review_request is None
    assert d.engine_session_id is None
    assert d.review_bundle is None
    assert d.worktree_owned is True
    assert d.verdict is None
    assert d.fix_of is None
    assert d.fix_round == 0


def test_v8_fields_round_trip():
    d = Delegation(
        agent="a",
        task="t",
        blocking=True,
        review_request={
            "reviewer_hint": "reviewer",
            "diff_ref": {"worktree_path": "/w", "branch": "b", "pr_url": None},
            "manifest_summary": "did the thing",
            "confidence": 0.8,
            "requested_at": "2026-09-11T00:00:00",
        },
    )
    data = d.to_dict()
    assert data["blocking"] is True
    assert data["review_request"]["reviewer_hint"] == "reviewer"
    back = Delegation.from_dict(data)
    assert back.blocking is True
    assert back.review_request is not None
    assert back.review_request["confidence"] == 0.8
    assert back.schema_version == 13


def test_waitset_fields_default():
    d = Delegation(agent="a", task="t")
    assert d.blocking is False
    assert d.review_request is None
    assert d.to_dict()["blocking"] is False
    assert d.to_dict()["review_request"] is None


def test_unknown_fields_still_dropped():
    """The unknown-field drop (gotcha #12) still holds on the v13 set.

    M2.1-follow-up update: v8 -> v9 (engine_session_id).
    Review Phase 1 update: v9 -> v10 (review_bundle).
    Worktree-policy update: v10 -> v11 (worktree_owned).
    M2.2 update: v11 -> v12 (verdict).
    M2.2 follow-up update: v12 -> v13 (fix_of + fix_round)."""
    d = Delegation.from_dict(_minimal_record(8, future_field="x", blocking=True))
    assert d.schema_version == 13
    assert d.blocking is True
    assert not hasattr(d, "future_field")
