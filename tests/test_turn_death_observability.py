"""Turn-death observability (incident 2026-09-11, slice 1).

Backend child ``c81d11745606`` hung ~17 min silent, then died on the
httpx 1000s client timeout with a bare ``[chat error: ReadTimeout: ]``:
* the chain-loop rejection of the re-dispatch was invisible
  server-side (returned to the LLM only — ``web.log`` had nothing);
* the 300s stall watchdog never fired because it only wrapped body
  chunks, not response-header wait.

Slice 1 closes both blind spots (no behaviour change to the HTTP
surface or the turn outcome):
* every ChainError 409 logs code + target + chain root;
* ``_send_message`` bounds the stream open with the stall budget
  (``stalled`` ``phase=headers``) and traces ``stream_opened`` /
  ``first_byte`` so the next silent death is classifiable from the
  trace alone.
"""

from __future__ import annotations

import asyncio as _asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.trace_log import TraceLog, read_trace


# ---------------------------------------------------------------------------
# Local helpers — mirrored from test_specialist_runtime.py
# ---------------------------------------------------------------------------


class _FakeStreamClient:
    """Replay fixed chunks (headers arrive immediately)."""

    def __init__(self, *chunks: str) -> None:
        self._chunks = chunks

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        chunks = self._chunks

        class _Resp:
            async def __aenter__(self) -> _Resp:
                return self

            async def __aexit__(self, *args: Any) -> bool:
                return False

            def raise_for_status(self) -> None:
                pass

            async def aiter_text(self) -> Any:
                for c in chunks:
                    yield c

        return _Resp()


class _HangingOpenClient:
    """Headers never arrive (the 2026-09-11 shape: hang sits in the
    stream open, outside the old body-only watchdog)."""

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        class _Resp:
            async def __aenter__(self) -> Any:
                await _asyncio.sleep(10.0)
                return self

            async def __aexit__(self, *args: Any) -> bool:
                return False

            def raise_for_status(self) -> None:
                pass

            async def aiter_text(self) -> Any:
                yield "{}"

        return _Resp()


class _Proc:
    def __init__(self, client: Any, session_id: str = "ses_probe") -> None:
        self._client = client
        self._session_id = session_id
        self.base_url = "http://mock"


def _terminal_chunk() -> str:
    return json.dumps({
        "info": {
            "role": "assistant",
            "time": {"created": 1, "completed": 2},
            "finish": "stop",
        },
        "parts": [{"type": "text", "text": "hello"}],
    })


# ---------------------------------------------------------------------------
# ChainError 409 logging
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


def test_chain_loop_rejection_logs_warning(client: TestClient, caplog):
    """A loop rejection keeps its 409 + 'rejected:' surface AND logs
    code + target + chain root server-side (was: LLM-only)."""
    r_parent = client.post("/api/v2/tasks", json={"task": "p", "agent": "backend"})
    assert r_parent.status_code == 200
    parent_id = r_parent.json()["delegation_id"]
    r_child = client.post(
        "/api/v2/tasks",
        json={"task": "c", "agent": "backend", "parent_task_id": parent_id},
    )
    assert r_child.status_code == 200
    child_id = r_child.json()["delegation_id"]

    with caplog.at_level("WARNING", logger="sweave.web.routers.delegations"):
        r = client.post(
            "/api/v2/tasks",
            json={"task": "retry", "agent": "backend", "parent_task_id": child_id},
        )
    assert r.status_code == 409
    assert "rejected: loop detected" in r.json()["detail"]
    logged = " ".join(rec.message for rec in caplog.records)
    assert "loop_detected" in logged
    assert "backend" in logged
    assert parent_id in logged


# ---------------------------------------------------------------------------
# Stream-open / first-byte instrumentation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_header_hang_fails_as_stall_with_phase(tmp_path: Path):
    """Headers never arriving trips the stall bound (was: invisible to
    the watchdog; httpx won minutes later with a bare ReadTimeout)."""
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc = _Proc(_HangingOpenClient())
    trace = TraceLog("d-hdrhang", base_dir=tmp_path)
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace,
        stall_seconds=0.2,
    )
    assert out.startswith("[chat error: stalled after ")
    events = {e["event"]: e for e in read_trace(trace.delegation_id, base_dir=tmp_path)}
    assert events["stalled"]["phase"] == "headers"
    assert "stream_opened" not in events
    assert "first_byte" not in events
    assert "output_text" not in events


@pytest.mark.asyncio
async def test_healthy_turn_traces_open_and_first_byte(tmp_path: Path):
    """A normal turn records stream_opened + first_byte (the events
    whose absence marks a header hang)."""
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc = _Proc(_FakeStreamClient(_terminal_chunk()))
    trace = TraceLog("d-openbyte", base_dir=tmp_path)
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace,
        stall_seconds=5.0,
    )
    assert out == "hello"
    events = {e["event"]: e for e in read_trace(trace.delegation_id, base_dir=tmp_path)}
    assert "wait_s" in events["stream_opened"]
    assert "latency_s" in events["first_byte"]
    assert events["first_byte"]["latency_s"] >= events["stream_opened"]["wait_s"]
