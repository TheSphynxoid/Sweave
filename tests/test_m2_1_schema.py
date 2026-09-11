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


def test_v1_record_loads_as_v8_with_waitset_defaults():
    """Full chain: a v1 record passes through every migration
    (v1→…→v7→v8) and lands with blocking False + review_request None."""
    d = Delegation.from_dict(_minimal_record(1))
    assert d.schema_version == SCHEMA_VERSION == 8
    assert d.blocking is False
    assert d.review_request is None
    # Earlier migrations still hold.
    assert d.estimate is None
    assert d.kind == "task"
    assert d.needs_attention is False
    assert d.archived is False


def test_v7_record_loads_as_v8_with_waitset_defaults():
    """Pre-M2.1 review records load with review_request None (a v7
    record in status review carries no request — step 4 attaches
    requests only to new transitions)."""
    d = Delegation.from_dict(_minimal_record(7, status="review"))
    assert d.schema_version == 8
    assert d.blocking is False
    assert d.review_request is None


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
    assert back.schema_version == 8


def test_waitset_fields_default():
    d = Delegation(agent="a", task="t")
    assert d.blocking is False
    assert d.review_request is None
    assert d.to_dict()["blocking"] is False
    assert d.to_dict()["review_request"] is None


def test_unknown_fields_still_dropped():
    """The unknown-field drop (gotcha #12) still holds on the v8 set."""
    d = Delegation.from_dict(_minimal_record(8, future_field="x", blocking=True))
    assert d.schema_version == 8
    assert d.blocking is True
    assert not hasattr(d, "future_field")
