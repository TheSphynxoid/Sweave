"""M2.1 review resolution seam (plan step 6) tests.

No new endpoint, no verdict payload (ruling 4): the orchestrator
resolves a review-request explicitly via ``defer(target=reviewer)``
(the reviewer child links ``parent_task_id`` to the review
delegation) or batched at wait-set settle (the synthesis prompt
surfaces pending review-requests of join-set children). Read side:
``review_request`` rides ``to_dict`` + the detail projection (the
M2.0 detail-fold precedent); unknown ids degrade to nulls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sweave.runtime.delegation_store import Delegation


# ---------------------------------------------------------------------------
# App fixture (same stub pattern as test_m2_0_estimation)
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
# Resolve-via-defer contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_via_defer_links_reviewer_child(monkeypatch):
    """The orchestrator resolves a review-request by deferring to
    the reviewer hint: the reviewer child links ``parent_task_id``
    to the review delegation (already possible — this pins the
    contract, one-authority rule: specialists never spawn
    reviewers)."""
    captured: list[tuple[str, dict]] = []

    async def fake_post(path: str, body: dict, token: str) -> dict:
        captured.append((path, body))
        return {"delegation_id": "del-reviewer-1", "status": "queued"}

    monkeypatch.setattr("sweave.mcp._http_post", fake_post)

    from mcp.types import CallToolRequestParams

    from sweave.mcp import _defer

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "reviewer",
            "task": "review the backend work",
            "caller_delegation_id": "del-review-1",
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is False
    assert captured[0][0] == "/api/v2/tasks"
    assert captured[0][1]["agent"] == "reviewer"
    assert captured[0][1]["parent_task_id"] == "del-review-1"


# ---------------------------------------------------------------------------
# Wait-set settle surfaces pending requests (synthesis prompt)
# ---------------------------------------------------------------------------


def _child_with_request() -> Delegation:
    return Delegation(
        agent="backend", task="build x", status="review",
        output="built x", blocking=True,
        review_request={
            "reviewer_hint": "reviewer",
            "diff_ref": {"worktree_path": "w", "branch": "b", "pr_url": None},
            "manifest_summary": "build x",
            "confidence": 0.8,
            "requested_at": "2026-09-12T00:00:00",
        },
    )


def test_synthesis_surfaces_pending_review_request():
    """A join-set child with a pending request surfaces it in the
    synthesis prompt (per-child line + Pending section with the
    resolve-via-defer contract)."""
    from sweave.chat.synthesis import build_synthesis_prompt

    prompt = build_synthesis_prompt(
        children=[_child_with_request()],
        original_user_message="build x please",
    )
    assert "review_requested" in prompt
    assert "reviewer_hint=reviewer" in prompt
    assert "Pending Review Requests" in prompt
    assert "defer(target=<reviewer_hint>" in prompt


def test_synthesis_without_requests_has_no_review_section():
    """Children without requests render exactly as before (no
    review lines, no Pending section)."""
    from sweave.chat.synthesis import build_synthesis_prompt

    prompt = build_synthesis_prompt(
        children=[
            Delegation(agent="backend", task="x", status="done",
                       output="ok", blocking=True)
        ],
        original_user_message="hi",
    )
    assert "review_requested" not in prompt
    assert "Pending Review Requests" not in prompt


# ---------------------------------------------------------------------------
# Detail fold: review_request rides the projection
# ---------------------------------------------------------------------------


def test_detail_projection_echoes_review_request(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    request = {
        "reviewer_hint": "reviewer",
        "diff_ref": {"worktree_path": "w", "branch": "b", "pr_url": None},
        "manifest_summary": "did x",
        "confidence": 0.9,
        "requested_at": "2026-09-12T00:00:00",
    }
    detail = render_detail_view(
        "d1", trace_dir=tmp_path / "traces", review_request=request
    )
    assert detail["review_request"] == request


def test_detail_projection_without_request_is_null(tmp_path: Path):
    from sweave.web.detail_view import render_detail_view

    detail = render_detail_view("d1", trace_dir=tmp_path / "traces")
    assert detail["review_request"] is None


def test_detail_endpoint_joins_record_review_request(client: TestClient):
    """GET .../detail carries the record's review_request (None on a
    fresh delegation — nothing finished yet)."""
    r = client.post("/api/v2/tasks", json={"task": "x", "agent": "backend"})
    assert r.status_code == 200, r.text
    did = r.json()["delegation_id"]
    detail = client.get(f"/api/delegations/{did}/detail")
    assert detail.status_code == 200
    assert detail.json()["review_request"] is None


def test_detail_endpoint_unknown_id_degrades_with_null_request(
    client: TestClient,
):
    """Unknown-id degrade contract holds (200 + nulls, per the M2.0
    amendment 6) — now including the review_request key."""
    detail = client.get("/api/delegations/does-not-exist/detail")
    assert detail.status_code == 200
    body = detail.json()
    assert body["review_request"] is None
    assert body["estimate_vs_actual"] == {
        "estimate": None, "actual": {"tokens": None, "seconds": None}
    }
