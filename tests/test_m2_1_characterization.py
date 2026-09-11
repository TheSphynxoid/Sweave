"""M2.1 characterization: pin the pre-M2.1 synthesis + gating behavior.

M2.1 amends M1.6 parent gating + M1.7 synthesis (highest regression
risk in the series per docs/M2_PLAN.md §4), so these tests run FIRST:
they pin what the code does TODAY, before the emission work builds on
it. Steps 4-5 will amend the behavior (and update the tests that pin
the old rule — each update cites the step).

Current behavior pinned here:
* ChatLoop._wait_for_children joins ALL children (no wait-set scoping)
  and treats ``review`` as settled (loop.py:365-372).
* JobRunner._wait_for_children joins ALL children and settles ONLY on
  ``done``/``failed`` — a child sitting in ``review`` wedges the
  parent until ``turn_timeout`` (job_runner.py:891/:898 — the
  mismatch M2.1 reconciles).
* Submit carries no ``blocking`` flag; MCP ``defer`` passes no
  ``blocking`` key; schema is v7 with no ``blocking``/``review_request``
  fields; ``promote`` has no review-request to preserve.
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
    """
    assert SCHEMA_VERSION == 8
    fields = set(Delegation.__dataclass_fields__)  # type: ignore[attr-defined]
    assert "blocking" in fields
    assert "review_request" in fields
    d = Delegation(agent="a", task="t")
    assert d.blocking is False
    assert d.review_request is None


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
    """Pin the :898 mismatch: a child in ``review`` does NOT settle
    the JobRunner parent gate — the wait burns the full timeout."""
    runner, stores, project_dir = _runner_with_store(turn_timeout=2.0)
    store = await _store_for(stores, project_dir)

    parent = Delegation(agent="alpha", task="parent", project_name="p")
    await store.add(parent)
    await store.add(
        Delegation(
            agent="beta", task="child", project_name="p",
            parent_task_id=parent.delegation_id, status="review",
        )
    )

    from sweave.runtime.trace_log import TraceLog

    trace = TraceLog(parent.delegation_id, base_dir=project_dir)
    t0 = time.monotonic()
    await runner._wait_for_children(parent, store, trace)
    elapsed = time.monotonic() - t0
    assert 1.5 <= elapsed <= 4.0, f"gate took {elapsed:.2f}s; expected ~2.0s timeout"
    events = [
        json.loads(line)
        for line in trace.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [e for e in events if e.get("event") == "children_settle_timeout"]
    assert not [e for e in events if e.get("event") == "children_settled"]


@pytest.mark.asyncio
async def test_chat_wait_settles_on_review_child():
    """Chat loop side of the mismatch: ``review`` already counts as
    settled in ChatLoop._wait_for_children (loop.py:372)."""
    from sweave.chat.loop import ChatLoop

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-char-chat-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = ChatLoop(
        project_manager=None,
        specialist_runtime=None,
        specialist_factory=lambda name: None,
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
    """Pre-M2.1: the chat wait has no wait-set — a still-running
    child gates the turn even though nothing opted into joining."""
    from sweave.chat.loop import ChatLoop

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-char-chat2-"))
    stores = PerProjectDelegationStores()
    store = await _store_for(stores, project_dir)
    chat = ChatLoop(
        project_manager=None,
        specialist_runtime=None,
        specialist_factory=lambda name: None,
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
    assert 1.5 <= elapsed <= 4.0, f"wait took {elapsed:.2f}s; expected timeout burn"
    assert len(children) == 1


@pytest.mark.asyncio
async def test_defer_sends_no_blocking_key(monkeypatch):
    """Pre-M2.1: MCP defer passes no ``blocking`` key to /api/v2/tasks."""
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
    """Pre-M2.1: TaskSubmitV2 carries no blocking field."""
    from sweave.web.routers.delegations import TaskSubmitV2

    assert "blocking" not in TaskSubmitV2.model_fields
