"""M2.1 characterization: pin the pre-M2.1 synthesis + gating behavior.

M2.1 amends M1.6 parent gating + M1.7 synthesis (highest regression
risk in the series per docs/M2_PLAN.md §4), so these tests run FIRST:
they pin what the code does TODAY, before the emission work builds on
it. Steps 4-5 will amend the behavior (and update the tests that pin
the old rule — each update cites the step).

Current behavior pinned here:
* ChatLoop._wait_for_children joins only BLOCKING children (M2.1
  step 4: wait-set scoping) and treats ``review`` as settled
  (loop.py shared JOIN_SETTLED_STATUSES rule).
* JobRunner._wait_for_children joins only BLOCKING children and
  settles on ``done``/``failed``/``review`` (M2.1 step 5: the :898
  fix — pre-M2.1 only done/failed settled, wedging the parent).
* Submit carries optional ``blocking`` (M2.1 step 3, default
  False); MCP ``defer`` passes it through only when supplied;
  schema is v8 with ``blocking``/``review_request`` fields (M2.1
  step 2); ``promote`` preserves the request (ruling 3).
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
    SCHEMA_VERSION,
)


def test_schema_is_v7_with_no_waitset_fields():
    """Pre-M2.1: schema v7; no blocking / review_request attributes.

    M2.1 step 2 update: schema is now v8 WITH the fields (defaults
    False/None). The pin moves to tests/test_m2_1_schema.py; this
    test now asserts the post-step-2 surface.
    M2.1-follow-up update: schema is now v9 WITH engine_session_id.
    Review Phase 1 update: schema is now v10 WITH review_bundle.
    Worktree-policy update: schema is now v11 WITH worktree_owned.
    M2.2 update: schema is now v12 WITH verdict.
    """
    assert SCHEMA_VERSION == 12
    fields = set(Delegation.__dataclass_fields__)  # type: ignore[attr-defined]
    assert "blocking" in fields
    assert "review_request" in fields
    assert "engine_session_id" in fields
    assert "review_bundle" in fields
    assert "worktree_owned" in fields
    assert "verdict" in fields
    d = Delegation(agent="a", task="t")
    assert d.blocking is False
    assert d.review_request is None
    assert d.worktree_owned is True
    assert d.verdict is None


def _runner_with_store(turn_timeout: float):
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-char-"))
    stores = PerProjectDelegationStores()
    runner = JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),
        delegation_stores=stores,
        turn_timeout=turn_timeout,
    )
    return runner, stores, project_dir


async def _store_for(stores: PerProjectDelegationStores, project_dir: Path):
    return await stores.for_project(project_dir)


@pytest.mark.asyncio
async def test_jobrunner_gate_wedges_on_review_child():
    """M2.1 step 5 update (the :898 fix): a BLOCKING child in
    ``review`` now SETTLES the JobRunner parent gate — the wait
    returns fast with ``children_settled`` (review count 1), no
    timeout. Pre-M2.1 this wedged the parent until ``turn_timeout``
    (only done/failed settled); the full matrix lives in
    tests/test_m2_1_waitset.py."""
    runner, stores, project_dir = _runner_with_store(turn_timeout=2.0)
    store = await _store_for(stores, project_dir)

    parent = Delegation(agent="alpha", task="parent", project_name="p")
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="child", project_name="p",
            parent_task_id=parent.delegation_id, status="review",
            blocking=True,
        )
    )

    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    t0 = time.monotonic()
    await runner._wait_for_children(parent, store, trace)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, f"gate took {elapsed:.2f}s; review should settle fast"
    events = [
        json.loads(line)
        for line in trace.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [e for e in events if e.get("event") == "children_settled"]
    assert not [e for e in events if e.get("event") == "children_settle_timeout"]


@pytest.mark.asyncio
async def test_chat_wait_settles_on_review_child():
    """Chat loop side of the mismatch: ``review`` already counts as
    settled in ChatLoop._wait_for_children (loop.py:372).

    M2.1 step 4 update: only JOIN-SET (``blocking=True``) children
    are waited on — the review child below opts in, so it still
    settles fast and is returned as synthesis input."""
    from sweave.chat.loop import ChatLoop

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-char-chat-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = ChatLoop(
        project_manager=None,
        specialist_runtime=None,
        specialist_factory=lambda name, project=None: None,
        project_dir_resolver=lambda name: None,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=5.0,
    )

    parent_id = "chat-parent-1"
    await store.add(
        Delegation(
            agent="backend", task="child", project_name="p",
            parent_task_id=parent_id, status="review",
            blocking=True,
        )
    )
    t0 = time.monotonic()
    children = await chat._wait_for_children(store, parent_id)
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"chat wait took {elapsed:.2f}s; review should settle fast"
    assert len(children) == 1
    assert children[0].status == "review"


@pytest.mark.asyncio
async def test_chat_wait_joins_every_child_unscoped():
    """M2.1 step 4 update: the chat wait is now wait-set-scoped — a
    still-running NON-BLOCKING child no longer gates the turn (the
    join set is empty, so the wait returns immediately with no
    deadline burn). A still-running BLOCKING child still gates
    (full matrix in tests/test_m2_1_waitset.py)."""
    from sweave.chat.loop import ChatLoop

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-char-chat2-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = ChatLoop(
        project_manager=None,
        specialist_runtime=None,
        specialist_factory=lambda name, project=None: None,
        project_dir_resolver=lambda name: None,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=2.0,
    )

    parent_id = "chat-parent-2"
    await store.add(
        Delegation(
            agent="backend", task="child", project_name="p",
            parent_task_id=parent_id, status="running",
        )
    )
    t0 = time.monotonic()
    children = await chat._wait_for_children(store, parent_id)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, f"wait took {elapsed:.2f}s; fire-and-forget must not gate"
    assert children == []


@pytest.mark.asyncio
async def test_defer_sends_no_blocking_key(monkeypatch):
    """MCP defer passes no ``blocking`` key when the caller omits it
    (M2.1 step 3: absent = fire-and-forget default; the key is only
    sent when explicitly supplied)."""
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
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is False
    assert "blocking" not in captured[0][1]


def test_submit_model_has_no_blocking():
    """M2.1 step 3 update (amended 2026-09-14): TaskSubmitV2 carries
    tri-state ``blocking`` (default None = resolve at submit: a child
    of a chat-turn delegation joins, everything else stays
    fire-and-forget). Absent flag without a chat parent =
    fire-and-forget, same as the pre-M2.1 behavior this test used
    to pin by absence."""
    from sweave.web.routers.delegations import TaskSubmitV2

    assert "blocking" in TaskSubmitV2.model_fields
    assert TaskSubmitV2.model_fields["blocking"].default is None
