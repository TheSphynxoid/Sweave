"""REVIEW Phase 1 step 2: record header + bundle pointer fold.

`GET .../detail` gains the `record` header (status/agent/task +
snippet/output summary/error/stamps/blocking/attention) and the
`review_bundle` pointer echo. Missing record degrades like missing
trace (nulls, never 500); snippet lengths are pinned.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sweave.web.detail_view import (
    OUTPUT_SUMMARY_CHARS,
    TASK_SNIPPET_CHARS,
    render_detail_view,
    render_record_header,
)


def test_snippet_lengths_are_pinned():
    """Executor picks, tests pin: task 140 (card rule), output 2000."""
    assert TASK_SNIPPET_CHARS == 140
    assert OUTPUT_SUMMARY_CHARS == 2000


def test_header_projects_all_fields():
    header = render_record_header(
        {
            "status": "review",
            "agent": "backend",
            "task": "do the thing",
            "output": "result text",
            "error": None,
            "created_at": "2026-09-12T00:00:00",
            "completed_at": "2026-09-12T00:01:00",
            "blocking": True,
            "needs_attention": True,
        }
    )
    assert header is not None
    assert header["status"] == "review"
    assert header["agent"] == "backend"
    assert header["task"] == "do the thing"
    assert header["task_snippet"] == "do the thing"
    assert header["output_summary"] == "result text"
    assert header["error"] is None
    assert header["created_at"] == "2026-09-12T00:00:00"
    assert header["completed_at"] == "2026-09-12T00:01:00"
    assert header["blocking"] is True
    assert header["needs_attention"] is True


def test_header_truncates_long_task_and_output():
    header = render_record_header(
        {
            "status": "done",
            "agent": "a",
            "task": "t" * 200,
            "output": "o" * 2500,
            "error": None,
            "created_at": None,
            "completed_at": None,
            "blocking": False,
            "needs_attention": False,
        }
    )
    assert header is not None
    assert len(header["task_snippet"]) > TASK_SNIPPET_CHARS
    assert header["task_snippet"].startswith("t" * TASK_SNIPPET_CHARS)
    assert "truncated" in header["task_snippet"]
    assert header["output_summary"].startswith("o" * OUTPUT_SUMMARY_CHARS)
    assert "truncated 500 chars" in header["output_summary"]


def test_header_none_without_record():
    assert render_record_header(None) is None


def test_detail_fold_carries_header_and_pointer(tmp_path: Path):
    detail = render_detail_view(
        "d1",
        trace_dir=tmp_path / "traces",
        record={"status": "review", "agent": "backend", "task": "t",
                "output": "out", "error": None, "created_at": None,
                "completed_at": None, "blocking": False,
                "needs_attention": True},
        review_bundle={"path": ".sweave/reviews/d1.diff", "bytes": 42,
                       "truncated": False, "scope": "worktree"},
    )
    assert detail["record"] is not None
    assert detail["record"]["status"] == "review"
    assert detail["record"]["needs_attention"] is True
    assert detail["review_bundle"] == {
        "path": ".sweave/reviews/d1.diff", "bytes": 42,
        "truncated": False, "scope": "worktree",
    }


def test_detail_fold_degrades_without_record(tmp_path: Path):
    detail = render_detail_view("ghost", trace_dir=tmp_path / "traces")
    assert detail["record"] is None
    assert detail["review_bundle"] is None


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


def test_detail_endpoint_carries_record_header(client: TestClient):
    r = client.post("/api/v2/tasks", json={"task": "x", "agent": "backend"})
    assert r.status_code == 200, r.text
    did = r.json()["delegation_id"]
    detail = client.get(f"/api/delegations/{did}/detail")
    assert detail.status_code == 200
    rec = detail.json()["record"]
    assert rec is not None
    assert rec["agent"] == "backend"
    assert rec["task"] == "x"
    assert rec["status"] in ("queued", "running", "review")
    assert detail.json()["review_bundle"] is None


def test_detail_endpoint_unknown_id_degrades(client: TestClient):
    r = client.get("/api/delegations/no-such-id/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["record"] is None
    assert body["review_bundle"] is None
